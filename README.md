# pdf-to-epub

把**扫描版 PDF 书籍**转成 **EPUB 3**，保留**原始封面**与**可导航目录**。
既是 DeepSeek Harness 的一个 **skill**（任何会话里说一句「把这本书转成 EPUB」即可），
也是一套可以脱离 DSH 单独调用的命令行工具。

Convert a scanned PDF book into a valid EPUB 3 that keeps the original cover
and a real, navigable table of contents.

> **想直接看操作说明？** 打开 [`MANUAL.txt`](MANUAL.txt)——纯文本手册，记事本可直接读，
> 涵盖换机配置、参数速查、工作流原理、故障排查、已知限制与实测数据。
> 下面的 README 是仓库概览，偏工程视角。

---

## 换电脑后怎么恢复

```powershell
# 1) 克隆到 DSH 的用户级 skill 目录（路径是约定，不要改）
git clone https://github.com/MatthewZhang31/PDF-TO-EPUB.git "$env:USERPROFILE\.dsh\skills\pdf-to-epub"

# 2) 一键安装依赖并自检
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.dsh\skills\pdf-to-epub\install.ps1"
```

不需要任何注册步骤：DSH 会扫描 `~/.dsh/skills/<名字>/SKILL.md`，新会话自动就能看到
`pdf-to-epub` 这个 skill。

### 注意事项

**① 克隆路径不能改。** DSH 只从 `$DSH_HOME/skills/`（默认 `~/.dsh/skills/`）发现 skill，
克隆到别处命令行仍可用，但不会自动出现在会话里。如果你的 `DSH_HOME` 环境变量指向别的位置，
要克隆到 `$DSH_HOME\skills\pdf-to-epub`——`install.ps1` 会检查并给出提示。

**② 这台电脑要先装好 DSH**，并装 Python 3.10+。安装脚本优先用 `python`，
找不到就回退到 `py -3`（Windows 官方安装器有时只注册后者）。

**③ pip 走镜像。** 默认 PyPI 在国内网络会超时，脚本默认用清华镜像。换了网络环境
（比如在国外）可以改：

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -Mirror https://pypi.org/simple
```

依赖已装好时用 `-SkipInstall` 跳过安装，只做自检。

**④ 转换产物不会跟着仓库走——这是最容易忽略的一点。** `.gitignore` 出于版权原因
排除了所有输出（EPUB、封面、图表裁切、`ocr.json` 等）。所以新电脑上：

- 之前转好的 EPUB **不在**新电脑上，需要自己另外拷贝（网盘/私有仓库）
- 重新转同一本书时 **OCR 要重跑**。这本书花了 74 分钟；如果不想重跑，
  把旧电脑的整个 `out\<书名>\` 目录拷过去即可（其中 `ocr.json` 就是 OCR 缓存，
  `assemble` 和 `build` 会直接复用）

**⑤ 执行策略。** 脚本必须加 `-ExecutionPolicy Bypass`，否则 PowerShell 会拒绝运行。

**⑥ 控制台编码。** Windows 控制台默认是 cp936，中文输出会崩。每次调用前设
`$env:PYTHONIOENCODING="utf-8"`（`install.ps1` 内部已设，但你自己敲命令时要设）。

**⑦ Git 凭据要重新配。** 这台机器上推送用的是 Windows 凭据管理器里的凭据，不会同步。
新电脑首次 `git push` 会要求登录。如果新电脑上 GitHub 连不上，见
[`references/troubleshooting.md`](references/troubleshooting.md) 里的本地代理方案——
注意那个代理设置写在 `.git/config`（机器本地），**同样不会同步过去**。

**⑧ 目标目录要为空。** `git clone` 要求目标目录不存在或为空，别预先建好带文件的目录。

**⑨ Python 太新可能缺 wheel。** `onnxruntime` 对新版本 Python 的支持会滞后，
若装不上 OCR 依赖，先用主流程（有文本层的 PDF 不需要 OCR），或换 Python 3.11/3.12。

## 日常使用

```powershell
$env:PYTHONIOENCODING="utf-8"
python "$env:USERPROFILE\.dsh\skills\pdf-to-epub\scripts\pdf2epub.py" all `
  --pdf "<PDF 绝对路径>" `
  --out "<输出目录>"
```

一条命令走完：分析 → 抽封面 → OCR（按需）→ 清洗重建 → 打包 → 校验。

### 常用参数

| 参数 | 作用 |
|---|---|
| `--text-source auto\|embedded\|ocr` | 逐页选择文本源；`auto` 优先用 OCR（默认） |
| `--force-ocr` | 对所有页做 OCR，包括纯图片页 |
| `--embedded-only` | 已有可用文本层的页不重跑 OCR（快速路径） |
| `--skip-ocr` | `all` 专用：完全不跑 OCR |
| `--workers N` | OCR 进程数，默认 `min(4, cpu/2)` |
| `--ocr-height N` | OCR 渲染高度，默认 2200px；小字号可提到 3000 |
| `--chapters FILE` | 手工指定/修正目录与章节边界（JSON） |
| `--title` `--author` `--lang` | 覆盖元数据 |
| `--include-front-matter` | 把封面/版权/印刷目录页也保留在正文流里 |

`--chapters` 的格式（`page` 是 **PDF 物理页**，不是印刷页码）：

```json
[
  {"title": "代序",   "page": 6,  "level": 1},
  {"title": "第一章", "page": 12, "level": 1},
  {"title": "第一节", "page": 15, "level": 2}
]
```

### 产物

| 文件 | 说明 |
|---|---|
| `<书名>.epub` | 成品电子书 |
| `cover.jpg` | 从扫描件提取的封面 |
| `toc.json` / `toc.md` | 恢复出的目录（结构化 / 可读） |
| `analysis.json` | PDF 分析报告（模式、书签、逐页质量） |
| `heading_candidates.json` | 疑似小标题候选，供人工或模型复核 |
| `epub_check.json` | EPUB 结构校验结果 |

## 工作流水线

```
PDF ──analyze──► analysis.json ─┐
    └─extract──► pages_embedded.json
    └─ocr──────► ocr.json ──────┤
                                 ├─assemble──► book.json ─► toc.json/toc.md
                                 │                └─► heading_candidates.json
                                 └─cover─────► cover.jpg
                                                    │
                              book.json + cover ────┴─build──► <book>.epub ─validate─► epub_check.json
```

每个阶段只通过 `--out` 里的 JSON 通信，因此任何一步都能单独重跑；OCR 按页缓存，
中断后继续不会重算。

### 设计要点

- **文本源按行融合，而不是二选一。** 现代 OCR（RapidOCR / PP-OCR）的标点与中英混排
  准确率明显高于 2000 年代扫描件内嵌的文本层（后者会把 `。` 认成 `J`、`：` 认成 `厂`），
  但内嵌层保存了脚注标记与页码。因此正文用 OCR，再通过行对齐把 `①②③` 脚注标记和
  `◎` 项目符号从内嵌层回填；同时反向用内嵌层纠正 OCR 把 `◎` 误认成 `③` 的错误。
- **目录以 PDF 书签为准**（通常与印刷目录一致）；没有书签时 OCR 目录页再在正文里
  定位标题。小标题不自动判定——字号与粗体信息常被扫描仪抹平，改为输出候选清单。
- **结构重建**：按行聚类修复跨行片段错序（对话破折号、制表位），按缩进/句末/居中/
  项目符号切分段落，剔除页眉页脚与页码，按标记拆分脚注块。
- **可验证**：`validate` 会把包内每个 XHTML/OPF/NCX 当 XML 解析一遍，
  `selftest.py` 覆盖行序、段落、清洗、标记回填、嵌套导航与 EPUB 往返。

## 环境要求

- Python 3.10+
- `pymupdf`、`pillow`、`numpy`（必需）
- `rapidocr-onnxruntime`、`onnxruntime`（可选；只有 PDF 没有可用文本层时才需要，
  纯 pip 安装、离线可用、CPU 即可）

安装务必走镜像，默认 PyPI 索引在本机网络下会超时
（`install.ps1` 已默认使用清华镜像）。

## 自检

```powershell
python "$env:USERPROFILE\.dsh\skills\pdf-to-epub\scripts\selftest.py"
```

不需要 PDF、不需要联网。**改动 `scripts/p2e/` 下任何代码后都应先跑一遍。**

## 对照两种文本源

```powershell
python "$env:USERPROFILE\.dsh\skills\pdf-to-epub\scripts\compare_sources.py" `
  --pdf book.pdf --out OUT --pages 10,11,165-170
```

会把内嵌层和一次全新 OCR 的同一页并排打印出来，用证据而不是猜测来决定 `--text-source`。

## 已知限制

- **字符级 OCR 误认无法用规则安全修正**，例如 `三个自人`→`三个白人`、
  `陷人`→`陷入`。工具不会用词典去"猜"，这类错误需要人工校对。
- 竖排文本、真正的多栏混排、表格不处理；插图不会被带入 EPUB（只带封面）。
- 目录页检测可能失手，此时整本书会退化成单章，需要 `--chapters` 手工指定。
- 封底推荐语、书脊、版权页这类艺术字体页面 OCR 质量差，可用 `--chapters` 剔除。

更多细节见 [`references/`](references/)：
[工作流](references/workflow.md)、
[OCR 与质量](references/ocr-and-quality.md)、
[故障排查](references/troubleshooting.md)。

## 为什么不直接用现成的插件

调研过 DSH 插件市场全部 3408 个插件，**没有任何 PDF→EPUB 转换插件**。相关的都是
给模型「读」文档用的（[`dsh-pdf-mineru`](https://github.com/Yurzi/dsh-pdf-mineru)
等），需要云 API Key 或自建 GPU 服务，且只输出 Markdown，没有封面、没有 EPUB、
没有导航。本工具完全本地、离线、CPU 可跑。

## 许可

MIT，见 [LICENSE](LICENSE)。

**注意**：本仓库只包含工具代码，不包含任何书籍内容。转换产物（EPUB、封面、
提取出的正文）受原书版权保护，请勿提交到公开仓库——`.gitignore` 已默认排除。
