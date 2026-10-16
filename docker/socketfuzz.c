/*
 * desock_dup.c from preeny, as packaged in AFL++ (utils/socket_fuzzing),
 * plus the additions from ROSA's aflpp-rosa.patch (accept sa_family,
 * getsockname, recv, recvmsg, getpeername).
 *
 * Extra (experiment-only, env-gated): DESOCK_EXIT_ON_REACCEPT=1 makes the
 * second accept() call _exit(0), so servers that loop forever on accept()
 * terminate after handling the single stdin-fed connection.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <pthread.h>
#include <signal.h>
#include <dlfcn.h>
#include <errno.h>
#include <poll.h>

int (*original_close)(int);
int (*original_dup2)(int, int);
static int accept_count = 0;
static int exit_on_reaccept = 0;

__attribute__((constructor)) void preeny_desock_dup_orig() {
  original_close = dlsym(RTLD_NEXT, "close");
  original_dup2 = dlsym(RTLD_NEXT, "dup2");
  exit_on_reaccept = getenv("DESOCK_EXIT_ON_REACCEPT") != NULL;
}

int close(int sockfd) {
  if (sockfd <= 2) { return 0; }
  return original_close(sockfd);
}

int dup2(int old, int new) {
  if (new <= 2) { return 0; }
  return original_dup2(old, new);
}

int accept(int sockfd, struct sockaddr *addr, socklen_t *addrlen) {
  (void)sockfd; (void)addrlen;
  if (exit_on_reaccept && accept_count++ > 0) { _exit(0); }
  if (addr != NULL) { addr->sa_family = AF_INET; }
  return 0;
}

int accept4(int sockfd, struct sockaddr *addr, socklen_t *addrlen, int flags) {
  (void)flags;
  return accept(sockfd, addr, addrlen);
}

int bind(int sockfd, const struct sockaddr *addr, socklen_t addrlen) {
  (void)sockfd; (void)addr; (void)addrlen;
  return 0;
}

int listen(int sockfd, int backlog) { (void)sockfd; (void)backlog; return 0; }

int setsockopt(int sockfd, int level, int optid, const void *optdata, socklen_t optdatalen) {
  (void)sockfd; (void)level; (void)optid; (void)optdata; (void)optdatalen;
  return 0;
}

int getsockname(int sockfd, struct sockaddr *addr, socklen_t *addrlen) {
  (void)sockfd;
  if (addr != NULL) {
    struct sockaddr_in target;
    socklen_t copylen = sizeof(target);
    if (*addrlen < sizeof(target)) { copylen = *addrlen; }
    memset(&target, 0, sizeof(target));
    target.sin_family = AF_INET;
    target.sin_addr.s_addr = inet_addr("127.0.0.1");
    target.sin_port = htons(8000);
    memcpy(addr, &target, copylen);
    *addrlen = copylen;
  }
  return 0;
}

int getpeername(int sockfd, struct sockaddr *addr, socklen_t *addrlen) {
  return getsockname(sockfd, addr, addrlen);
}

ssize_t recv(int sockfd, void *buf, size_t len, int flags) {
  (void)sockfd; (void)flags;
  return read(0, buf, len);
}

ssize_t recvfrom(int sockfd, void *buf, size_t len, int flags, struct sockaddr *src, socklen_t *srclen) {
  (void)src; (void)srclen;
  return recv(sockfd, buf, len, flags);
}

ssize_t recvmsg(int sockfd, struct msghdr *msg, int flags) {
  (void)sockfd; (void)flags;
  return read(0, msg->msg_iov->iov_base, msg->msg_iov->iov_len);
}

ssize_t send(int sockfd, const void *buf, size_t len, int flags) {
  (void)sockfd; (void)flags;
  return write(1, buf, len);
}

ssize_t sendto(int sockfd, const void *buf, size_t len, int flags, const struct sockaddr *dst, socklen_t dstlen) {
  (void)dst; (void)dstlen;
  return send(sockfd, buf, len, flags);
}
