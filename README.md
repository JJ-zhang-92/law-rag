# Chinese Law RAG

本地中文法律法规检索增强生成（RAG）工具。2,449 部中国法律法规 + 13,746 条向量索引 + HTTP API，**零云服务**，任意 LLM 可用。

## 数据库版本

| 项目 | 详情 |
|------|------|
| 数据源 | [国家法律法规数据库](https://flk.npc.gov.cn) |
| 上游仓库 | [qundao/law-book](https://github.com/qundao/law-book) |
| 法律数量 | 2,449 部 |
| 法规条目 | 13,746 条（按法条切分，66 万字符） |
| **最后更新** | **2026-06-20**（上游每周自动同步官方数据库） |

## 快速开始

### 前置条件

- Python 3.10+
- [Ollama](https://ollama.com)（用于文本嵌入）

### 安装

```bash
git clone https://github.com/YOUR_USERNAME/law-rag.git
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

### 检索

```bash
# 命令行（输出可直接贴入 LLM prompt）
python query.py "夫妻共同债务的认定标准"
python query.py "劳动争议 加班费 计算基数"

# HTTP API（先启动服务）
python server.py &
# 浏览器访问: http://localhost:8720?q=劳动合同 试用期
# curl:      curl "http://localhost:8720?q=失业保险费率&format=json"

# 作为 Python 模块
from search import search
results = search("你的法律问题", top_k=5)
```

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
| `index.py` | 构建 ChromaDB 向量索引 |
| `search.py` | 检索函数（可 import） |
| `query.py` | CLI 工具，输出可贴入 LLM prompt |
| `server.py` | HTTP API 服务（端口 8720） |
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
