# Chinese Law RAG

本地中文法律法规检索增强生成（RAG）工具。2,449 部中国法律法规 + **125,185 条按法条切分的向量索引** + HTTP API，**零云服务**，任意 LLM 可用。

## 数据库版本

| 项目 | 详情 |
|------|------|
| 数据源 | [国家法律法规数据库](https://flk.npc.gov.cn) |
| 上游仓库 | [qundao/law-book](https://github.com/qundao/law-book) |
| 法律数量 | 2,449 部全国性法规 + **1,977 部江浙沪地方法规**（其中 1,100 部为目录占位，见下） |
| 向量条目 | **125,185 条**（按"第X条"逐条切分，`article` 元数据规范化） |
| **最后更新** | **2026-07-10**（按条重建索引 + 精确查条 + 定向补库 + 地方法规占位标记） |

### 本次更新要点（2026-07）

- **按条切分修复**：修正 `split_by_article` 丢失条号的根因 bug，`article` 元数据由 `-` 规范化为 `第X条`（民法典 152 块→1260 条）；向量总数 40,155→125,185，语义检索更聚焦。
- **精确查条**：`search.get_article(law, no)` 按"法典名+条号"精确取全文，支持中文数字，绕开语义误命中。
- **定向补库**：`fetch_law_by_name.py`（flk 官方校验）+ `index_append.py`（`add_law` 增量入库，幂等·不重建全库）。
- **地方法规占位**：1,100 部地方法规仅有目录（flk 无正文），标 `text_status='目录占位'`，**按需现场爬取回填**（`backfill_local_regs.py`）。

## 快速开始

### 前置条件

- Python 3.10+
- [Ollama](https://ollama.com)（用于文本嵌入）

### 安装

```bash
git clone https://github.com/JJ-zhang-92/law-rag.git
cd law-rag

# 下载法律文本库
# Windows PowerShell:
Invoke-WebRequest "https://codeload.github.com/qundao/law-book/zip/refs/heads/main" -OutFile law-book.zip
Expand-Archive law-book.zip .
Move-Item law-book-main/content law-book

# macOS / Linux:
# curl -L "https://codeload.github.com/qundao/law-book/zip/refs/heads/main" -o law-book.zip
# unzip law-book.zip && mv law-book-main/content law-book

# 安装依赖
pip install -r requirements.txt

# 拉取嵌入模型（约 274 MB，仅需一次）
ollama pull nomic-embed-text

# 构建向量索引（约 30-60 分钟，仅需一次）
python index.py
```

### 检索（主入口：search.py）

```bash
# 语义检索（纯向量，cosine 相似度）
python search.py "试用期最长多久" --top-k 5

# 作为 Python 模块
from search import search
results = search("你的法律问题", top_k=5)

# 精确按条号取全文（绕开语义误命中，推荐用于文书起草引用）
from search import get_article
print(get_article("民法典", 563))        # 支持简称 + int
print(get_article("公司法", "第一条"))    # 支持 '第X条' 字符串
```

#### 按需补库（RAG 缺失某法时）

```bash
# 1) 定向爬取官方数据库 flk.npc.gov.cn，做权威校验（名称/时效/条文数/目录）
python fetch_law_by_name.py "中华人民共和国公司法"

# 2) 取到正文后增量入库（不重建全库，秒级；幂等）
python index_append.py "中华人民共和国公司法" 正文.txt 法律
```

> **覆盖边界**：`fetch_law_by_name.py` 仅覆盖 flk 收录的
> 宪法/法律/行政法规/监察法规/司法解释/地方性法规。
> **国务院部门规章**（如《律师执业管理办法》《律师和律师事务所执业证书管理办法》）
> 不在 flk，须由上层 `ensure_law` 走通用官网 fetch（gov.cn / 部委官网）兜底并 `index_append` 入库。
> flk 正文文件受 OFD 阅读器 + 内网 OBS 签名直链保护，脚本通常无法直取正文，
> 故 `fetch_law_by_name` 提供官方元数据 + 条文目录 + 时效性 + source_url，
> 正文由权威镜像补齐并以 `article_count` 交叉校验。

#### 决策层级（纯规则，零 LLM 调用）

整个检索流水线中，**没有任何 chat/instruction LLM 被调用**。策略选择、评分排序、结果组织全部由确定性的规则和算法完成。Ollama 仅用于将文本转为向量嵌入。

| 层级 | 机制 | 耗时 | 说明 |
|------|------|------|------|
| **策略选择** | 正则匹配 | <1ms | 从查询中识别法典名+条号/对比词/列举词 |
| **嵌入编码** | Ollama nomic-embed-text | ~200ms | 文本转向量，不做语义判断 |
| **重排序** | BM25 算法 | ~10ms | 纯统计算分，不调 LLM |
| **结果聚合** | 规则去重/聚类 | <1ms | 按法典或相似度合并分组 |

#### 四种检索策略

| 策略 | 触发条件 | 行为 | 示例 |
|---|---|---|---|
| `semantic` | 默认（无关键词匹配） | 纯向量检索，cosine 相似度 | `"试用期最长多久"` |
| `exact` | 含已知法典名 + 条文号 | BM25 粗排 top-200 → 向量精排 | `"民法典第577条"` |
| `compare` | 含"区别/对比/vs/还是/或者" | 拆分两个子查询 → 分别检索 → 合并去重 | `"加班费和奖金有什么区别"` |
| `enumerate` | 含"有哪些/哪些/列举/什么情况" | 扩大 top-k → 按法典聚类分组 | `"哪些情形可以解除劳动合同"` |

#### 增量追加新法规（不重建索引）

```bash
# 取到正文后逐部增量入库（幂等，秒级）
python index_append.py "法律名称" 正文.txt 分类
```

### ⚠️ 隔离红线

`law_regulations`（法律法条·125K 条·768维 nomic-embed）与 `patent_ref`（专利对比文件）**严格隔离**，严禁跨库交叉检索。知识产权保护法律可在 `patent_ref` 复制备份。

### 命令行 / HTTP API（备用）

### 更新

```bash
# 每月执行一次，自动同步最新法律法规
# 策略：优先 GitHub 同步 → 兜底爬虫增量更新 → 自动重建索引
python update.py

# 或手动分步：
python crawler.py          # 增量爬虫，只抓新法
python index.py            # 重建向量索引
```

## 架构

```
法律文本                  嵌入模型               向量索引
┌──────────┐    ┌──────────────────┐    ┌──────────┐
│ law-book │───▶│ nomic-embed-text  │───▶│ ChromaDB │
│ 2449部法规│    │ (Ollama 本地运行)   │    │125185向量 │
└──────────┘    └──────────────────┘    └────┬─────┘
                       │                     │
        ┌──────────────┘          ┌──────────┴──────────┐
        ▼                         ▼                     ▼
 ┌─────────────┐          ┌──────────┐          ┌──────────┐
 │ crawler.py  │          │ query.py │          │server.py │
 │ 增量爬虫     │          │ CLI 工具   │          │HTTP API  │
 │ flk.npc.gov │          └──────────┘          └────┬─────┘
 └─────────────┘                                     │
         │                         ┌─────────────────┤
         ▼                         ▼                 ▼
  官方数据库同步           ChatGPT/Claude       opencode
                          (复制粘贴法条)       (直接查询)
```

## API

```
GET http://localhost:8720?q=关键词&top=5&format=text|json
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `q` | (必填) | 搜索关键词 |
| `top` | 5 | 返回条数 |
| `format` | text | `text` = 纯文本（贴入 prompt），`json` = 结构化 |

## 更新策略

```
python update.py
        │
        ├─ 1. 下载 qundao/law-book 最新 zip（主通道）
        │      上游每周从 flk.npc.gov.cn 自动同步
        │
        ├─ 2. 爬虫增量检查（兜底通道）
        │      调用 flk.npc.gov.cn API 获取新法速递
        │      对比本地已有法律，下载缺失项
        │
        ├─ 3. 重建 ChromaDB 向量索引
        │
        └─ 4. 自动更新 README 版本戳
```

每月执行一次即可。中国立法节奏不快，一个季度通常只有几部新法。

## 文件说明

| 文件 | 用途 |
|------|------|
| `search.py` | **主入口** — `search()` 语义检索 + `get_article(law, no)` 精确按条号取全文 |
| `fetch_law_by_name.py` | 定向按名爬取 flk：官方名称/时效/条文数/目录 + 最佳努力正文 |
| `index_append.py` | 增量入库单部法律（幂等，不重建全库）：`add_law()` |
| `backfill_local_regs.py` | 地方法规"目录占位"按需回填：`backfill()` + `--list` 清单 |
| `index.py` | 构建/重建 ChromaDB 向量索引（全量） |
| `query.py` | CLI 工具，输出可贴入 LLM prompt |
| `server.py` | HTTP API 服务（端口 8720·备用，纯语义） |
| `crawler.py` | 按日期增量爬虫，从 flk.npc.gov.cn 抓新法 |
| `browser_crawl.py` | 按省份爬取地方性法规（Playwright） |
| `update.py` | 一键更新：下载 + 爬虫 + 重建索引 |
| `requirements.txt` | Python 依赖 |

> 注：README 早期版本提及的 `tools/retrieve.py`（4策略检索引擎）实际未实现，已由
> `search.py`（`search` + `get_article`）+ `fetch_law_by_name.py` + `index_append.py` 取代。

> **地方法规"目录占位"**：库中 1100 部江浙沪地方法规仅有目录、无正文
> （flk `content` 空 + docx 内网 OBS 签名锁定，无法预抓），已标记 `metadata.text_status='目录占位'`。
> 策略为**按需回填**：使用时用权威镜像取全文 → `backfill_local_regs.backfill()` 校验入库并解除占位标记。
> 清单：`python backfill_local_regs.py --list [省份]`（上海28/浙江463/江苏609）。
> 判断某法是否占位：`from search import stub_status; stub_status("上海市体育发展条例")`。

## 技术栈

- **文本嵌入**: Ollama + nomic-embed-text（768 维，274 MB）
- **向量数据库**: ChromaDB（余弦相似度检索）
- **法律来源**: [国家法律法规数据库](https://flk.npc.gov.cn)，通过 [qundao/law-book](https://github.com/qundao/law-book) 自动同步
- **爬虫 fallback**: 直连 flk.npc.gov.cn API，增量获取

## License

MIT
