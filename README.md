# Let's Read

为一本书写读书笔记的 Claude skill：前面是一篇书评体正文，后面是写给日后写论文、做研究用的附录。

两个设计前提：

- **引文必须是原书的原话。** 每条引文从预处理后的原文逐字复制，交付前用脚本逐条核对页码与措辞。
- **笔记的附录是数据。** frontmatter 和附录二、三、四有固定字段和封闭的标签词表，由脚本校验，目的是让许多本书的笔记日后能归并成跨书的概念库。正文是写给人读的，不受约束。

## 目录

```
.claude-plugin/
  plugin.json         插件清单：名字、版本、作者
build.sh              打包成可安装的 .plugin（打包前自检）
release.sh            发版前检查并推送 main
.github/workflows/    推送后由 GitHub 自动打标签、建 Release
CHANGELOG.md          历次更新记录
CLAUDE.md             在 Claude 会话里维护本仓库的步骤
SKILL.md              主流程：预处理 → 问阅读方式 → 逐章读并写卡片 → 检索背景 → 定大纲 → 写作 → 核对 → 交付归档
references/
  appendix.md         frontmatter 字段表、附录一至四的规范、概念命名规则
  chapter-card.md     章节卡片与全书地图的模板
  structure.md        书评正文的结构与写法，含范本拆解
  style.md            语言、引用格式、套话清单
  book-types.md       按书的类型调整切入角度
  parallel-reading.md 并行阅读的分组与派发
  format-example.md   填好的笔记格式样例（内容为虚构，仅示意结构）
scripts/
  prepare_book.py     抽文本、修正部首字符、对齐印刷页码、切章、生成概况
  locate.py           在全书里查句子，返回印刷页码、章节和上下文
  verify_quotes.py    逐条核对引文与页码
  check_meta.py       校验 frontmatter 与附录的字段、列和标签词表
  check_style.py      检查正文的套话、格式和页码写法
  check_all.py        对知识库里所有书的笔记跑一遍核对
  archive_book.py     把一本书打包成知识库里的书目录，另打一个脚本包
  build_kb.py         合并各书概念成概念页，生成总目录、待读清单和合并记录
```

## 安装

```bash
./build.sh
```

产物在 `dist/lets-read-<版本号>.plugin`，拖进 Claude 安装即可。插件名和 skill 名都是 `lets-read`（2.1.0 之前叫 `lets-read-v2`）。

依赖：Python 3，`pyyaml`（可选，缺失时 `check_meta.py` 会用内置的简化解析器）；`prepare_book.py` 另需 PDF/EPUB 相关库；扫描版用 RapidOCR 识别（`rapidocr_onnxruntime`，脚本会自动安装，模型随包附带），装不上时退回 Tesseract。

## 改动与发版

更新记录见 [CHANGELOG.md](CHANGELOG.md)；每个版本的安装包附在 GitHub 的 Releases 页面上。

1. 改 `SKILL.md`、`references/` 或 `scripts/`
2. 在 `.claude-plugin/plugin.json` 里把 `version` 升一位（规则见 CHANGELOG.md 开头：只修 bug 升末位，改了笔记规范升中位）
3. 在 `CHANGELOG.md` 最上面加一节 `## vX.Y.Z — 日期`，写清这一版改了什么
4. `./build.sh` 打包并自检，装上 `dist/` 里新的 `.plugin` 试用
5. 提交后发版：

```bash
git add -A && git commit -m "v2.1.0：这一版改了什么"
./release.sh
```

`release.sh` 检查版本号没有发布过、更新记录里有这一版、改动都已提交、本地不落后于远端，并打包自检，全部通过后推送 main。推送后 GitHub 自动打标签、创建 Release：说明取自 CHANGELOG 里这一节，附件是 `.plugin`。版本号没升的推送不会发新版。

补发历史版本时，在 GitHub 的 Actions 页面选“发版”，点 Run workflow，填要发布的提交或分支。v1.0.0 的文件存档在 `archive/v1.0.0` 分支上。

打过 tag 之后，任何时候都能取回那个版本：`git checkout v2.0.0`。

笔记格式变过之后这一点尤其有用：旧笔记是按当时的规范写的，`git show v2.0.0:references/appendix.md` 可以查到它当时要求什么，不必靠回忆。

## 核对流程

笔记写完、或日后在 Obsidian 等编辑器里改过之后：

```bash
python scripts/verify_quotes.py <笔记.md> <source目录> --title "书名"
python scripts/check_meta.py  <笔记.md>
python scripts/check_style.py <笔记.md>
```

整个知识库一次过，再合并概念：

```bash
python scripts/check_all.py <知识库目录> --quotes
python scripts/build_kb.py  <知识库目录>
```

笔记归档后，知识库里那份就是唯一正本，在 Obsidian 里直接编辑；改坏的几种方式都会被上面的脚本抓住。用户在 Claude 里说“检查一下”，就是跑这两步。

## 知识库结构

知识库放在 Obsidian 仓库里（如 `读书库/`）：

```
<知识库>/
  books/<书名>/
    <书名>.md      带 frontmatter 的笔记正本（2.2.0 之前叫 note.md，build_kb.py --rename-notes 可改名）
    cards/         全部章节卡片
    book_map.md
    sources.md
    questions.md   「读时的疑问」+「后来的批注」
    source/        预处理产物，原样保留
  concepts/<归并名>.md   跨书概念页：两行 lets-read:auto 标记之间由脚本生成，“综述”留给人写
  index.md        总目录（脚本生成）
  待读.md         多本书共同推荐、还没读的书（脚本生成）
  log.md          合并记录（只追加）
  知识库说明.md   哪些能改、哪些由脚本生成
  .lets-read/     检查与合并用的脚本副本（Obsidian 不显示）
```

`source/` 不要删：`meta.json`、`pages.jsonl`、`chapters.tsv` 是 `locate.py` 和 `verify_quotes.py` 的运行前提，少一个这本书就既无法回查原文、也无法重新核对引文。

## 概念命名

附录二和正文里的概念名**一律用作者的说法**，不要换成领域通行叫法——读书笔记是关于这一本书的文件。归并名放在 frontmatter 的 `canonical` 里，只服务于跨书连接，不回写进笔记：

```yaml
  - name: 坐板凳（benching）/ 板凳术    # 作者的说法，照抄附录二第一列
    canonical: 板凳制度                # 归并名，只用于跨书归并
    aliases: [benching, 坐板凳, 板凳术]
```

本书自创的概念（「猎身」「虚拟短缺」「印度三角」这类作者的发明），`canonical` 与 `name` 写同一个词。
