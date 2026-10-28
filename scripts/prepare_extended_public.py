"""Build attributed public API documents and a pending-review QA pack.

Generated DOCX files are conversions, not independent original publications.
This script never creates the formal tune/holdout files or marks human approval.
"""

import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from docx import Document
from langchain_community.document_loaders import Docx2txtLoader
from prepare_public_demo import BASE, CACHE, PAGES, REVISION, digest, download, make_docx

from kb.config import load_config
from kb.ingest.loaders_docx import load_docx
from kb.ingest.metadata import FIELDS, scan

RAW = Path("data/raw/public_extended")
REVIEW = Path("data/review/public-v1")
K8S_URL = (
    "https://raw.githubusercontent.com/kubernetes/kubernetes/v1.34.0/api/openapi-spec/swagger.json"
)
SCHEMAS = """Pod PodSpec PodStatus Container ContainerPort ContainerState ContainerStatus
EnvVar EnvFromSource Volume VolumeMount ResourceRequirements Probe HTTPGetAction
Service ServiceSpec ServicePort ConfigMap Secret PersistentVolume PersistentVolumeSpec
PersistentVolumeClaim PersistentVolumeClaimSpec Node NodeSpec NodeStatus Namespace
NamespaceSpec ServiceAccount SecurityContext""".split()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


def fastapi_document(relative):
    source = CACHE / "fastapi" / relative
    markdown = download(f"{BASE}/{relative}", source)
    code_sources = []

    def include(match):
        relative_code = match[1]
        target = CACHE / relative_code
        code = download(f"{BASE}/{relative_code}", target)
        code_sources.append({"url": f"{BASE}/{relative_code}", "sha256": digest(target)})
        return f"\n```python\n{code}\n```\n"

    markdown = re.sub(r"\{\* ../../(docs_src/\S+\.py)[^}]*\*\}", include, markdown)
    if "{*" in markdown:
        raise ValueError(f"Unresolved code include: {relative}")
    slug = relative.removeprefix("docs/zh/docs/tutorial/").removesuffix(".md").replace("/", "-")
    title = next(line.lstrip("# ") for line in markdown.splitlines() if line.startswith("# "))
    title = re.sub(r"\{.*?\}", "", title).strip()
    target = RAW / f"FastAPI-{slug}.docx"
    if not target.exists():
        make_docx(markdown, target, f"{BASE}/{relative}")
    return {
        "id": f"fastapi-{slug}",
        "title": f"FastAPI · {title}",
        "file_name": f"public_extended/{target.name}",
        "source_url": f"{BASE}/{relative}",
        "source_path": str(source),
        "source_sha256": digest(source),
        "artifact_sha256": digest(target),
        "revision": REVISION,
        "license": "MIT",
        "code_sources": code_sources,
        "doc_type": "接口说明书",
        "project_id": "FASTAPI",
        "project_name": "FastAPI",
        "transformation": "官方独立教程转换为 DOCX；展开代码；省略图片",
    }


def field_type(prop):
    if "$ref" in prop:
        return prop["$ref"].split("/")[-1]
    if prop.get("type") == "array":
        return "array<" + field_type(prop["items"]) + ">"
    return prop.get("type", "object") + (":" + prop["format"] if prop.get("format") else "")


def kubernetes_documents(spec):
    result = []
    for name in SCHEMAS:
        key = f"io.k8s.api.core.v1.{name}"
        schema = spec["definitions"][key]
        target = RAW / f"Kubernetes-{name}.docx"
        doc = Document()
        doc.add_heading(f"Kubernetes v1.34.0 · {name}", 0)
        doc.add_paragraph("官方 OpenAPI 定义转换件。字段含义沿用英文原文；不是合同或内部项目文件。")
        doc.add_heading("对象说明", 1)
        doc.add_paragraph(schema.get("description", ""))
        doc.add_paragraph("完整定义标识符：" + key)
        doc.add_heading("字段参数表", 1)
        table = doc.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        for cell, text in zip(
            table.rows[0].cells, ["字段", "类型", "required声明", "含义与约束"], strict=True
        ):
            cell.text = text
        for field, prop in schema["properties"].items():
            values = [
                field,
                field_type(prop),
                "是" if field in schema.get("required", []) else "否（未列入required）",
                prop.get("description", "未提供说明"),
            ]
            for cell, text in zip(table.add_row().cells, values, strict=True):
                cell.text = text
        doc.add_heading("来源与边界", 1)
        doc.add_paragraph(K8S_URL + "#/definitions/" + key)
        doc.add_paragraph(
            "Kubernetes Authors，Apache-2.0。转换：JSON schema 字段转表格。"
            "required 仅表示此 schema 声明，不代替服务端所有校验规则。"
        )
        if not target.exists():
            doc.save(target)
        result.append(
            {
                "id": f"k8s-{name}",
                "title": f"Kubernetes · {name}",
                "file_name": f"public_extended/{target.name}",
                "source_url": K8S_URL + "#/definitions/" + key,
                "source_path": str(CACHE / "kubernetes-v1.34.0-swagger.json"),
                "source_sha256": digest(CACHE / "kubernetes-v1.34.0-swagger.json"),
                "artifact_sha256": digest(target),
                "revision": "v1.34.0",
                "license": "Apache-2.0",
                "doc_type": "接口说明书",
                "project_id": "KUBERNETES",
                "project_name": "Kubernetes",
                "transformation": "单一官方 OpenAPI 规范的30个对象转换件，非独立原始规范",
                "schema_key": key,
            }
        )
    return result


def qa_pack(documents, spec):
    rows = []

    def add(question, answer, evidence, kind, item):
        rows.append(
            {
                "id": f"PUBLIC-QA-{len(rows) + 1:03d}",
                "question": question,
                "reference_answer": answer,
                "reference_contexts": [
                    {
                        "doc_name": item["file_name"],
                        "source_url": item["source_url"],
                        "text": evidence,
                    }
                ]
                if evidence
                else [],
                "doc_type": item["doc_type"],
                "project_name": item["project_name"],
                "question_type": kind,
                "human_reviewed": False,
                "review_status": "pending",
                "source_group": item.get("schema_key", item["source_url"]),
            }
        )

    k8s = [d for d in documents if "schema_key" in d]
    for item in k8s[:25]:
        name = item["id"].removeprefix("k8s-")
        schema = spec["definitions"][item["schema_key"]]
        parsed = load_docx(Path("data/raw") / item["file_name"])
        evidence = next(b.text for b in parsed.blocks if b.kind == "table")
        props = [
            (k, v)
            for k, v in schema["properties"].items()
            if k not in {"apiVersion", "kind", "metadata"}
        ]
        for field, prop in props[:2]:
            required = "是" if field in schema.get("required", []) else "否（未列入required）"
            add(
                f"Kubernetes v1.34.0 的 {name}.{field} 的类型和 required 声明分别是什么？",
                f"类型：{field_type(prop)}；required声明：{required}。",
                evidence,
                "表格类",
                item,
            )
        if len(props) < 2:
            raise ValueError(f"Not enough distinct fields: {name}")
    for item in k8s[:25]:
        name = item["id"].removeprefix("k8s-")
        schema = spec["definitions"][item["schema_key"]]
        props = [
            (k, v)
            for k, v in schema["properties"].items()
            if v.get("description") and k not in {"apiVersion", "kind", "metadata"}
        ]
        for field, prop in props[:2]:
            add(
                f"Kubernetes v1.34.0 中 {name}.{field} 字段的含义与使用约束是什么？",
                prop["description"],
                f"{name}.{field}: {prop['description']}",
                "关键词/标识符类",
                item,
            )
        if len(props) < 2:
            raise ValueError(f"Not enough described fields: {name}")
    concepts = 0
    for item in [d for d in documents if d["project_name"] == "FastAPI"]:
        parsed = load_docx(Path("data/raw") / item["file_name"])
        groups = {}
        for b in parsed.blocks:
            if b.kind == "text" and b.section_path and "来源与转换" not in b.section_path:
                groups.setdefault(b.section_path, []).append(b.text)
        for section, parts in groups.items():
            evidence = "\n".join(parts)
            if len(evidence) < 180 or concepts >= 80:
                continue
            add(
                f"{item['title']}文档的「{section}」章节介绍了哪些用法和注意事项？",
                evidence,
                evidence,
                "概念类",
                item,
            )
            concepts += 1
            if (
                sum(
                    r["reference_contexts"]
                    and r["reference_contexts"][0]["doc_name"] == item["file_name"]
                    for r in rows
                )
                >= 3
            ):
                break
    if concepts != 80:
        raise ValueError(f"Need 80 source-backed concept candidates, got {concepts}")
    absent = [
        "员工工资发放日期",
        "报销审批人",
        "内部VPN密码",
        "生产数据库地址",
        "年度销售额",
        "明年采购预算",
        "项目验收签字人",
        "保密协议到期日",
        "会议室预订规则",
        "服务器管理员电话",
        "内部工单处理SLA",
        "员工入职人数",
        "云资源实际月账单",
        "最新生产故障根因",
        "研发部门绩效标准",
        "内部仓库访问令牌",
        "本月未结清发票",
        "合同收款银行账号",
        "在职员工家庭住址",
        "公司食堂开放时间",
    ]
    for topic in absent:
        add(f"本企业的{topic}是什么？", "知识库中未检索到相关内容", "", "无答案类", documents[0])
    assert len(rows) == 200 and len({r["question"] for r in rows}) == 200
    jsonl(REVIEW / "qa_candidates_200.jsonl", rows)
    jsonl(
        REVIEW / "qa_reviews.template.jsonl",
        [{"id": r["id"], "approved": None, "reviewer": "", "notes": ""} for r in rows],
    )
    return rows


def compare_tables(items):
    results = []
    for item in items[:20]:
        path = Path("data/raw") / item["file_name"]
        plain = Docx2txtLoader(str(path)).load()[0].page_content
        tables = [b.text for b in load_docx(path).blocks if b.kind == "table"]
        result = {
            "id": item["id"],
            "source_url": item["source_url"],
            "doc_name": item["file_name"],
            "docx2txt": plain,
            "structured_tables": tables,
            "table_count": len(tables),
            "human_reviewed": False,
            "row_column_relations_checked": None,
        }
        results.append(result)
    jsonl(REVIEW / "table_comparison_20.jsonl", results)
    lines = [
        "# 20 个参数表解析对照",
        "",
        "docx2txt 输出为纯文本，结构化解析保留表头和行列。"
        "两种方式都保留文字；结构丢失不自动等同于问答失败。",
        "",
    ]
    for r in results:
        lines.extend(
            [
                f"## {r['id']}",
                "",
                f"来源：{r['source_url']}",
                "",
                "结构化表格：",
                "",
                *r["structured_tables"],
                "",
                "docx2txt 片段：",
                "",
                "```text",
                r["docx2txt"][:1800],
                "```",
                "",
                "审核结论：________；审核人：________。",
                "",
            ]
        )
    (REVIEW / "table_comparison_20.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    REVIEW.mkdir(parents=True, exist_ok=True)
    tree = json.loads(
        download(
            f"https://api.github.com/repos/fastapi/fastapi/git/trees/{REVISION}?recursive=1",
            CACHE / "fastapi-tree.json",
        )
    )
    paths = [
        x["path"]
        for x in tree["tree"]
        if x["path"].startswith("docs/zh/docs/tutorial/")
        and x["path"].endswith(".md")
        and not x["path"].endswith("/index.md")
        and x["path"].removeprefix("docs/zh/docs/tutorial/").removesuffix(".md") not in PAGES
    ]
    assert len(paths) == 43
    with ThreadPoolExecutor(max_workers=4) as pool:
        fastapi = list(pool.map(fastapi_document, paths))
    spec = json.loads(download(K8S_URL, CACHE / "kubernetes-v1.34.0-swagger.json"))
    download(
        "https://raw.githubusercontent.com/kubernetes/kubernetes/v1.34.0/LICENSE",
        RAW / "LICENSE-Kubernetes.txt",
    )
    k8s = kubernetes_documents(spec)
    documents = fastapi + k8s
    # Merge into the public manifest without discarding original demo sources.
    manifest_path = Path("data/demo/manifest.json")
    manifest = json.loads(manifest_path.read_text())
    names = {d["file_name"] for d in documents}
    manifest["documents"] = [
        d for d in manifest["documents"] if d["file_name"] not in names
    ] + documents
    manifest["human_reviewed"] = False
    manifest["review_status"] = "expanded_pending_review"
    save_json(manifest_path, manifest)
    save_json(REVIEW / "source_manifest.json", manifest)
    config = load_config("ingest")
    rows = scan(Path(config["raw_dir"]), Path(config["registry"]), config)
    by_name = {d["file_name"]: d for d in documents}
    for row in rows:
        if row["file_name"] in by_name:
            row.update(
                {
                    k: by_name[row["file_name"]][k]
                    for k in ["doc_type", "project_id", "project_name"]
                }
            )
    with Path(config["registry"]).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    candidates = qa_pack(documents, spec)
    compare_tables(k8s)
    save_json(
        REVIEW / "summary.json",
        {
            "documents": len(manifest["documents"]),
            "independent_fastapi_pages": 48,
            "kubernetes_definition_extracts": 30,
            "procurement_announcements": 2,
            "qa_candidates": len(candidates),
            "human_reviewed": False,
            "formal_split_created": False,
        },
    )
    print(
        json.dumps(
            {"documents": len(rows), "qa_candidates": len(candidates), "review_dir": str(REVIEW)}
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
