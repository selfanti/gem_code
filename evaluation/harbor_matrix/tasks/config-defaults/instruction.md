Fix the configuration loader in `/workspace/evaluation_fixture/src/config_loader.py`.

Requirements:
- Keep `OPENAI_API_KEY` and `OPENAI_BASE_URL` required.
- Default `skills_dir` to `{workdir}/.agents` when `SKILLS_DIR` is blank.
- Treat `MCP_CONFIG_PATH` as optional and return `None` when blank.
- Default `memory_compaction_path` to `~/.gem_code/projects` when blank.
- Do not change the tests.
