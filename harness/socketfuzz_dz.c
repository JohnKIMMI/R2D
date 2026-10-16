/*
 * ROSA socket-fuzzing preload (desock) -- socketfuzz_plus + DESOCK_STDIN_PIPE
 * (for boost.asio servers such as domoticz; see stdin_to_pipe below).
 * Base: AFL++ utils/socket_fuzzing + ROSA's aflpp-rosa.patch additions
 * (accept sa_family, getsockname, getpeername, recv, recvfrom, recvmsg,
 * send, sendto). Plus three experiment-only, env-gated additions:
 *   DESOCK_EXIT_ON_REACCEPT=1  second accept() -> _exit(0) (single-shot)
 *   DESOCK_NO_DAEMON=1         first fork() -> return 0 (skip daemonize;
 *                              keeps later per-connection forks real)
 *   (fdopen of the connection fd is downgraded so servers that reopen the
 *    accepted socket with "r+" do not fail on read-only stdin)
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <dlfcn.h>
#include <poll.h>
#include <errno.h>
#include <sys/stat.h>

/* DESOCK_STDIN_PIPE=1 (domoticz / boost.asio servers): asio registers the accepted
 * fd with epoll, and epoll refuses regular files (EPERM) -- which is what AFL hands
 * us as stdin. On the first accept() we therefore copy the current stdin contents
 * into a pipe and dup2() the pipe's read end onto fd 0. Done lazily (not in the
 * constructor) so it runs in each fork-server child, after AFL has written the
 * input of that run. Inputs beyond the pipe capacity (raised to 1 MiB) are cut. */
static int (*orig_close_fd)(int);
static void stdin_to_pipe(void) {
  struct stat st;
  if (fstat(0, &st) != 0 || S_ISFIFO(st.st_mode) || S_ISSOCK(st.st_mode)) return;
  if (!orig_close_fd) orig_close_fd = dlsym(RTLD_NEXT, "close");
  size_t cap = 1 << 20, n = 0; ssize_t r;
  char *buf = malloc(cap); if (!buf) return;
  lseek(0, 0, SEEK_SET);
  while (n < cap && (r = read(0, buf + n, cap - n)) > 0) n += (size_t)r;
  int p[2];
  if (pipe(p) != 0) { free(buf); return; }
  fcntl(p[1], F_SETPIPE_SZ, (int)(n > 65536 ? n : 65536));
  fcntl(p[1], F_SETFL, fcntl(p[1], F_GETFL) | O_NONBLOCK);     /* never block: truncate instead */
  size_t w = 0;
  while (w < n) { ssize_t k = write(p[1], buf + w, n - w); if (k <= 0) break; w += (size_t)k; }
  orig_close_fd(p[1]);
  dup2(p[0], 0); orig_close_fd(p[0]);
  free(buf);
}

static int accept_count = 0;
static int exit_on_reaccept = 0;
static int no_daemon = 0;
static int exit_on_close = 0;
static int fork_count = 0;
static int listen_fds[16]; static int n_listen = 0;
static int listen_served = 0;
static int conn_ever = 0;
static int is_listen_fd(int fd){ for(int i=0;i<n_listen;i++) if(listen_fds[i]==fd) return 1; return 0; }
static int (*original_close)(int);
static FILE *(*original_fdopen)(int, const char *);
static pid_t (*original_fork)(void);

__attribute__((constructor)) void preeny_desock_init(void) {
  original_close  = dlsym(RTLD_NEXT, "close");
  original_fdopen = dlsym(RTLD_NEXT, "fdopen");
  original_fork   = dlsym(RTLD_NEXT, "fork");
  exit_on_reaccept = getenv("DESOCK_EXIT_ON_REACCEPT") != NULL;
  no_daemon        = getenv("DESOCK_NO_DAEMON") != NULL;
  exit_on_close    = getenv("DESOCK_EXIT_ON_CLOSE") != NULL;
}
int shutdown(int sockfd, int how) {
  (void)how;
  if (sockfd == 0 && conn_ever && exit_on_close) { fflush(NULL); _exit(0); }
  return 0;
}

/* DESOCK_EXIT_ON_CLOSE=1: the run is over when the server closes (or shuts down)
 * the accepted connection, i.e. fd 0. For asio servers, which re-arm accept()
 * before serving the request, this replaces DESOCK_EXIT_ON_REACCEPT. */
int close(int sockfd) {
  if (sockfd == 0 && conn_ever && exit_on_close) { fflush(NULL); _exit(0); }
  if (sockfd <= 2) return 0;
  if (!original_close) original_close = dlsym(RTLD_NEXT, "close");
  return original_close(sockfd);
}

int accept(int sockfd, struct sockaddr *addr, socklen_t *addrlen) {
  (void)sockfd; (void)addrlen;
  if (!conn_ever && getenv("DESOCK_STDIN_PIPE") != NULL) stdin_to_pipe();
  if (getenv("DESOCK_ACCEPT_ONESHOT") != NULL) {
    /* 1st accept -> new conn (fd 0); next N -> EAGAIN so the server services
     * the existing connection; after the budget -> exit. */
    if (accept_count++ == 0) { conn_ever = 1; if (addr) addr->sa_family = AF_INET; return 0; }
    if (accept_count > 200) { _exit(0); }
    errno = EAGAIN; return -1;
  }
  if (exit_on_reaccept && accept_count++ > 0) { _exit(0); }
  conn_ever = 1;
  if (addr != NULL) addr->sa_family = AF_INET;
  return 0;
}
int accept4(int s, struct sockaddr *a, socklen_t *l, int f) { (void)f; return accept(s,a,l); }
int bind(int s, const struct sockaddr *a, socklen_t l) { (void)s;(void)a;(void)l; return 0; }
int listen(int s, int b) { (void)b; if(n_listen<16) listen_fds[n_listen++]=s; return 0; }
int setsockopt(int s,int l,int o,const void *d,socklen_t dl){(void)s;(void)l;(void)o;(void)d;(void)dl;return 0;}

int getsockname(int sockfd, struct sockaddr *addr, socklen_t *addrlen) {
  (void)sockfd;
  if (addr != NULL) {
    struct sockaddr_in t; socklen_t cl = sizeof(t);
    if (*addrlen < sizeof(t)) cl = *addrlen;
    memset(&t,0,sizeof(t)); t.sin_family=AF_INET;
    t.sin_addr.s_addr=inet_addr("127.0.0.1"); t.sin_port=htons(8000);
    memcpy(addr,&t,cl); *addrlen=cl;
  }
  return 0;
}
int getpeername(int s, struct sockaddr *a, socklen_t *l) { return getsockname(s,a,l); }

/* Only the fake connection (fd 0) is redirected to stdin/stdout. Every other
 * socket -- socketpairs the program uses internally (domoticz StoppableTask),
 * netlink, real client sockets -- keeps its real recv/send. socketfuzz_plus
 * redirected every fd, which let the program's own socketpair drain our input. */
static ssize_t (*o_recv)(int,void*,size_t,int);
static ssize_t (*o_recvfrom)(int,void*,size_t,int,struct sockaddr*,socklen_t*);
static ssize_t (*o_recvmsg)(int,struct msghdr*,int);
static ssize_t (*o_send)(int,const void*,size_t,int);
static ssize_t (*o_sendto)(int,const void*,size_t,int,const struct sockaddr*,socklen_t);
ssize_t recv(int s,void *b,size_t n,int f){
  if (s==0) return read(0,b,n);
  if(!o_recv) o_recv=dlsym(RTLD_NEXT,"recv");
  return o_recv(s,b,n,f);}
ssize_t recvfrom(int s,void *b,size_t n,int f,struct sockaddr *sa,socklen_t *sl){
  if (s==0) return read(0,b,n);
  if(!o_recvfrom) o_recvfrom=dlsym(RTLD_NEXT,"recvfrom");
  return o_recvfrom(s,b,n,f,sa,sl);}
ssize_t recvmsg(int s,struct msghdr *m,int f){
  if (s==0) return read(0,m->msg_iov->iov_base,m->msg_iov->iov_len);
  if(!o_recvmsg) o_recvmsg=dlsym(RTLD_NEXT,"recvmsg");
  return o_recvmsg(s,m,f);}
ssize_t send(int s,const void *b,size_t n,int f){
  if (s==0) return write(1,b,n);
  if(!o_send) o_send=dlsym(RTLD_NEXT,"send");
  return o_send(s,b,n,f);}
ssize_t sendto(int s,const void *b,size_t n,int f,const struct sockaddr *d,socklen_t dl){
  if (s==0) return write(1,b,n);
  if(!o_sendto) o_sendto=dlsym(RTLD_NEXT,"sendto");
  return o_sendto(s,b,n,f,d,dl);}

/* Only downgrade the accepted connection fd (0) when reopened read-write. */
FILE *fdopen(int fd, const char *mode) {
  if (!original_fdopen) original_fdopen = dlsym(RTLD_NEXT, "fdopen");
  if (fd == 0 && mode && (strchr(mode,'+') || mode[0]=='w' || mode[0]=='a'))
    return original_fdopen(0, "r");
  return original_fdopen(fd, mode);
}

/* Skip only the daemonize fork (first call): return 0 so the parent branch
 * (which exits) is not taken and the process stays under our control.
 * Later forks (per-connection handlers) proceed normally. */
pid_t fork(void) {
  if (getenv("DESOCK_NO_FORK") != NULL) return 0;          /* single-process */
  if (no_daemon && fork_count++ == 0) return 0;            /* skip daemonize only */
  if (!original_fork) original_fork = dlsym(RTLD_NEXT, "fork");
  return original_fork();
}


/* For poll()-driven servers (thttpd): report every watched fd as readable so
 * the event loop proceeds to read the stdin-fed request. Env-gated. */
int poll(struct pollfd *fds, nfds_t nfds, int timeout) {
  (void)timeout;
  if (getenv("DESOCK_THTTPD") != NULL) {
    /* Event-loop server (thttpd): make the listen fd readable only ONCE (so a
     * single accept happens), keep connection fds readable, and exit once the
     * connection has been serviced and nothing is left ready. */
    int ready = 0;
    for (nfds_t i = 0; i < nfds; i++) {
      if (is_listen_fd((int)fds[i].fd)) {
        if (!listen_served) { fds[i].revents = POLLIN; ready++; }
        else fds[i].revents = 0;
      } else { fds[i].revents = POLLIN; ready++; }
    }
    if (!listen_served && n_listen > 0) listen_served = 1;
    if (ready == 0 && conn_ever) _exit(0);
    return ready;
  }
  if (getenv("DESOCK_POLL_READY") != NULL) {
    for (nfds_t i = 0; i < nfds; i++) fds[i].revents = POLLIN;
    return (int)nfds;
  }
  static int (*orig)(struct pollfd *, nfds_t, int) = 0;
  if (!orig) orig = dlsym(RTLD_NEXT, "poll");
  return orig(fds, nfds, timeout);
}
