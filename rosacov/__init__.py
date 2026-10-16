"""rosacov -- ROSA campaigns and DynamoRIO (drcov) coverage, end to end.

targets.toml -> rosacov config  -> ROSA config file
             -> rosacov campaign -> rosa / rosa-simulate / rosa-evaluate (+ live watcher)
             -> rosacov replay   -> drcov logs -> block lists
             -> rosacov metrics  -> per-input records, unions, summary
             -> rosacov check    -> compare two runs (live vs batch, old vs new)

Standard library only: it runs inside the campaign containers as well as on the host.
"""
__version__ = "0.1.0"
