This task is executed by gem-code's custom local Harbor environment.

The directory only needs to exist because Harbor task validation expects
an `environment/` folder. The actual runtime setup happens in
`evaluation/local_harbor_environment.py`, which copies the current
repository checkout and then overlays `workspace_template/` into
`/workspace/evaluation_fixture`.
