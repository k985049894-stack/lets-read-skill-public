# 维护说明（给 Claude）

本仓库是插件 `lets-read`（Let's Read 读书笔记 skill）的公开源码仓库。公开插件由这里打包；维护时修改本仓库，再打包发版，不要修改已安装的同步副本。

## 目录与打包

仓库是平铺的，`build.sh` 打包时转换成插件结构：

| 仓库里 | 插件里 |
|---|---|
| `.claude-plugin/plugin.json`、`README.md` | 插件根目录 |
| `SKILL.md`、`references/`、`scripts/` | `skills/lets-read/` |

`CHANGELOG.md`、`CLAUDE.md`、`build.sh`、`release.sh`、`.github/` 只留在仓库，不进插件。本仓库从 v2.4.0 的源码建立独立历史，不导入旧提交、旧分支或旧标签。不要改插件名和 skill 名（`lets-read`；2.1.0 之前叫 `lets-read-v2`）。

## 每次改动的步骤

1. 先和用户说清楚要改什么、为什么改，用户同意后再动手。
2. 修改 `SKILL.md`、`references/` 或 `scripts/`。
3. 测试：
   - `./build.sh` 会做语法检查，并用 `check_meta.py` 检查 `references/format-example.md`。
   - 改了哪个脚本，就针对改动构造能触发问题的样例跑一遍，确认修好了，也没有误伤正常写法。
   - 改了规范（`references/`、`SKILL.md`）时，同步更新 `format-example.md` 和 `check_meta.py`，保持三者一致。
4. 在 `.claude-plugin/plugin.json` 里升版本号：只修 bug 或改措辞升末位，笔记规范有变化升中位，整体重做升首位。
5. 在 `CHANGELOG.md` 最上面加一节 `## vX.Y.Z — YYYY-MM-DD`，按“规范 / 脚本 / 文档 / 维护”分类，写清改了什么、为什么改。这一节会原样成为 Release 说明，写给用户看，语言平实。
6. 如果改动影响 README 里的说明（目录、流程、规则），一并更新 README。
7. 提交前检查暂存区和提交消息，不得包含会话链接、账号凭据或个人邮箱；作者与提交者均使用 GitHub noreply 邮箱。然后执行 `git add -A && git commit -m "vX.Y.Z：一句话摘要"`，推送前再核对提交元数据。
8. 发版：`./release.sh`。它会检查版本号未用过、CHANGELOG 有这一节、工作区干净、本地不落后于远端，打包自检通过后推送 main。推送后由 GitHub Actions（`.github/workflows/release.yml`）自动打标签、创建 Release 并上传 `.plugin`。
9. 一两分钟后用 `gh api repos/k985049894-stack/lets-read-skill-public/releases/tags/vX.Y.Z` 确认 Release 已经生成；没生成就用 `gh api repos/k985049894-stack/lets-read-skill-public/actions/runs` 查运行结果，把失败原因告诉用户。
10. 用 SendUserFile 把 `dist/lets-read-X.Y.Z.plugin` 发给用户安装，回复里用几句话说这一版改了什么，并给出 Release 链接。

## 环境注意

- 推送前 `release.sh` 会先 `git fetch origin main`；如果远端有新提交，先核对变更，再整合更新。
- 打标签和创建 Release 交给 GitHub Actions；工具返回权限错误时，遵守当前账号与会话的权限边界。
- 自动发版失败时，修好原因后再推送一次即可（版本号没发布过就会重试）。
- 需要手动运行“发版”流程（重跑、补发历史版本）时，会话可以用 REST 触发：
  `gh api --method POST repos/k985049894-stack/lets-read-skill-public/actions/workflows/release.yml/dispatches -f ref=main -f 'inputs[ref]=<提交或分支>' -f 'inputs[version]=<仅 skill 版需要>'`
- 本仓库是公开的。不要提交用户的读书笔记、书的原文、预处理产物、凭据文件或会话日志。
