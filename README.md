# Chinese Law RAG

本地中文法律法规检索增强生成（RAG）工具。2,449 部中国法律法规 + 13,746 条向量索引 + HTTP API，**零云服务**，任意 LLM 可用。

## 数据库版本

| 项目 | 详情 |
|------|------|
| 数据源 | [国家法律法规数据库](https://flk.npc.gov.cn) |
| 上游仓库 | [qundao/law-book](https://github.com/qundao/law-book) |
| 法律数量 | 2,449 部全国性法规 + **1,977 部江浙沪地方法规**（实时爬取） |
| 法规条目 | **39,055 条**（按法条切分） |
| **最后更新** | **2026-07-02**（全国法规: law-book 周更 + 地方法规: Playwright 实时爬取 flk.npc.gov.cn） |

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

### 检索（主入口：tools/retrieve.py）

```bash
# 语义检索（默认 — 纯向量，cosine 相似度）
python tools/retrieve.py query --collection law_regulations --persist C:\.opencode\law-rag\chroma_db "试用期最长多久"

# 自动策略选择（纯规则·<1ms·不调LLM）
# exact(条文) / compare(对比) / enumerate(列举) / semantic(语义)
# 例：含"民法典第577条" → 自动走 BM25+向量 混合精确查找
python tools/retrieve.py query --collection law_regulations --persist C:\.opencode\law-rag\chroma_db "民法典第577条"

# 作为 Python 模块
from search import search
results = search("你的法律问题", top_k=5)
```

#### 检索策略选择器

| 策略 | 触发条件 | 行为 |
|---|---|---|
| `exact` | 含法典名+条文号（如"劳动合同法第19条"）| BM25 粗排 top-200 → 向量精排 |
| `compare` | 含对比关键词（区别/vs/对比/还是/或者）| 拆为两个子查询 → 分别检索 → 合并去重 |
| `enumerate` | 含列举关键词（有哪些/哪些/列举/什么情况）| 扩大 top-k → 按法典聚类分组 |
| `semantic` | 以上都不匹配 | 纯向量检索（当前行为·不变）|

#### 增量追加新法规（不重建索引）

```bash
# 从官网爬取新法条 → 逐条切分到 docs.txt → 一行追加
python tools/retrieve.py index --collection law_regulations --persist C:\.opencode\law-rag\chroma_db --docs-file 新法条.txt
```

### ⚠️ 隔离红线

`law_regulations`（法律法条·39K 条·768维 nomic-embed）与 `patent_ref`（专利对比文件）**严格隔离**，严禁跨库交叉检索。知识产权保护法律可在 `patent_ref` 复制备份。

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
│ 2449部法规│    │ (Ollama 本地运行)   │    │ 13746向量 │
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
| `tools/retrieve.py` | **主入口** — 统一检索层（chromadb+nomic-embed·4种策略选择器·增量追加·BM25回退）|
| `index.py` | 构建 ChromaDB 向量索引 |
| `search.py` | 检索函数（可 import） |
| `query.py` | CLI 工具，输出可贴入 LLM prompt |
| `server.py` | HTTP API 服务（端口 8720·备用） |
| `crawler.py` | 增量爬虫，从 flk.npc.gov.cn 抓新法 |
| `update.py` | 一键更新：下载 + 爬虫 + 重建索引 |
| `requirements.txt` | Python 依赖 |

## 技术栈

- **文本嵌入**: Ollama + nomic-embed-text（768 维，274 MB）
- **向量数据库**: ChromaDB（余弦相似度检索）
- **法律来源**: [国家法律法规数据库](https://flk.npc.gov.cn)，通过 [qundao/law-book](https://github.com/qundao/law-book) 自动同步
- **爬虫 fallback**: 直连 flk.npc.gov.cn API，增量获取

## License

MIT
