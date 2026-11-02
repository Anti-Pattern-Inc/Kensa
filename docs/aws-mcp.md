# AWS MCP Server

日本語: [ja/aws-mcp.md](ja/aws-mcp.md)

By default, kensa bundles a public endpoint managed by AWS (the AWS MCP Server) in an **unauthenticated, documentation-search-only** form (`aws-docs`). A setup that can call APIs with your AWS credentials is provided separately as an **optional** part (`aws-mcp`). In both cases `scan-aws` does the reading of the live environment. `kensa*.py` never calls MCP. Everything works with the scanner and the interview alone, without MCP.

## Default (bundled): `aws-docs` — documentation search only, unauthenticated

- **Config**: `.mcp.json` (for Claude Code; `type: http`) and `mcp.json` (for Agent Plugins; `type: streamable-http`). Both are only a remote connection to `https://aws-mcp.us-east-1.api.aws/mcp`, and use no credentials, no profile and no `uvx`. The connection is made when the plugin is enabled.
- **Tools used**: `aws___search_documentation` (search) and `aws___read_documentation` (fetch a page), to look up the official documentation for individual items (Phase 1 of `SKILL.md`). In a supporting role: `aws___list_regions`, `aws___get_regional_availability`, `aws___retrieve_skill`.
- **Tools not used**: `aws___run_script` (runs Python in a sandbox) and `aws___get_presigned_url`. Both have `readOnlyHint=false`, and the official description says API execution requires IAM authentication. This setup passes no credentials, so it does not touch your AWS account. `SKILL.md` also tells the agent not to use them.
- **What was verified** (2026-11-02, connected without authentication): `initialize` and `tools/list` succeeded, and 8 tools were visible. `readOnlyHint` is `true` for `aws___search_documentation`, `aws___read_documentation`, `aws___get_tasks`, `aws___list_regions`, `aws___get_regional_availability` and `aws___retrieve_skill`, and `false` for `aws___run_script` and `aws___get_presigned_url`. **Not verified**: what happens when `aws___run_script` is called without authentication (it was not called), the quality and speed of search and fetch, and changes on the AWS side (the endpoint and the tools can change).
- **What leaves your machine**: search terms and the URLs of the pages you fetch are sent to the AWS endpoint. Scan results and account information are not sent (`SKILL.md` tells the agent not to put account IDs, resource names or internal information in search terms). The tool definitions (8 of them) use a little of the conversation context.
- **Turning it off**: in Claude Code, disable `aws-docs` from `/mcp`, or delete `.mcp.json`.

## Optional: `aws-mcp` — IAM-authenticated (can also run APIs)

A setup for when you want to run extra read-only API calls through `aws___run_script` (Phase 2 of `SKILL.md`; the `aws` CLI can substitute for it). **It is not bundled by default**: it can run Python with your IAM permissions, so with an administrator profile it could also write. In an environment without `uvx` or AWS credentials it would also be a source of startup errors.

When you want it, add the `mcpServers` of `optional/aws-mcp.json` to the MCP settings of the agent you use (SigV4; it needs `uvx` and AWS CLI credentials). The proxy is `mcp-proxy-for-aws-cli` (a distribution with pinned dependencies, which the AWS README recommends for `uvx`), pinned to a version. **The same tool names would appear twice, so disable `aws-docs` when you add it.**

- **Narrowing to read-only**: the proxy's `--read-only` is a **per-tool** filter that hides tools lacking `readOnlyHint=true`; it does not restrict the API calls made inside `aws___run_script` to reads. AWS states that IAM keys such as `aws-mcp:CallReadOnlyTool` have no effect. Only the permissions (IAM) of the downstream AWS services can narrow the kinds of operations.
- **How to connect**: give `--profile` **exactly one** read-only profile (if you give several, the agent can switch between them on each call). Attach the Deny from [Permissions (IAM)](aws-iam.md). If you want a Deny to apply only to calls that come through MCP, the condition key `aws:ViaAWSMCPService` is available (AWS's troubleshooting page has an example that denies `Action: *`). The default region can be set with `--metadata AWS_REGION=ap-northeast-1` (if unset, the value of `--region` is used).
- **If you add `--read-only`**: add it to `args`. For example: `["mcp-proxy-for-aws-cli@1.7.0", "https://aws-mcp.us-east-1.api.aws/mcp", "--profile", "readonly", "--read-only"]`. Per the check above, `aws___run_script` and `aws___get_presigned_url` have `readOnlyHint=false`, so they should be hidden while documentation search remains. **Whether the proxy really behaves that way should be confirmed in the tool list after connecting** (not verified). If `aws___run_script` is hidden, do the extra reads in Phase 2 with the `aws` CLI.
- The legacy tool `aws___call_aws` is not in the official current tool list.
