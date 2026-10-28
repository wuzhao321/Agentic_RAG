"""Read only the selected local Dify app/dataset configuration, excluding credentials."""

import argparse
import json
import subprocess
from pathlib import Path

import yaml

from kb.eval.runs import write_json

QUERY = """
import json,sys
from app_factory import create_app
_,app=create_app()
with app.app_context():
 from core.db.session_factory import session_factory
 from sqlalchemy import select,func
 from models.model import App
 from models.workflow import Workflow
 from models.dataset import Dataset,Document,DocumentSegment,DatasetProcessRule
 with session_factory.create_session() as s:
  a=s.get(App,sys.argv[1]); w=s.get(Workflow,a.workflow_id); ds=s.get(Dataset,sys.argv[2])
  docs=list(s.scalars(select(Document).where(Document.dataset_id==ds.id)))
  assert a.tenant_id==ds.tenant_id
  out={"dify_version":"1.18.0","app_id":a.id,"app_name":a.name,
       "workflow_id":w.id,"workflow_version":w.version,"graph":json.loads(w.graph),
       "features":json.loads(w.features),"dataset_id":ds.id,
       "embedding_model":ds.embedding_model,"embedding_provider":ds.embedding_model_provider,
       "retrieval_model":ds.retrieval_model,
       "process_rules":[r.to_dict() for r in s.scalars(select(DatasetProcessRule)
                                                 .where(DatasetProcessRule.dataset_id==ds.id))],
       "documents":[{"id":d.id,"name":d.name,"status":str(d.indexing_status),
                     "word_count":d.word_count} for d in docs],
       "segment_count":s.scalar(select(func.count()).select_from(DocumentSegment)
                                .where(DocumentSegment.dataset_id==ds.id))}
  print(json.dumps(out,ensure_ascii=False))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app_id")
    parser.add_argument("dataset_id")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--docker", default="/Applications/Docker.app/Contents/Resources/bin/docker"
    )
    args = parser.parse_args()
    result = subprocess.run(
        [
            args.docker,
            "compose",
            "-p",
            "kb-dify",
            "exec",
            "-T",
            "api",
            "/app/api/.venv/bin/python",
            "-",
            args.app_id,
            args.dataset_id,
        ],
        input=QUERY,
        text=True,
        capture_output=True,
        cwd=".tools/dify/docker",
        check=True,
    )
    snapshot = json.loads(result.stdout)
    write_json(args.output, snapshot)
    dsl = {
        "kind": "app",
        "version": "0.3.1",
        "dependencies": [],
        "app": {
            "name": snapshot["app_name"],
            "mode": "workflow",
            "icon": "📚",
            "icon_background": "#E4E7FF",
            "description": "已发布公开开发对照流程的无凭据快照",
        },
        "workflow": {
            "graph": snapshot["graph"],
            "features": snapshot["features"],
            "environment_variables": [],
            "conversation_variables": [],
        },
    }
    args.output.with_suffix(".yml").write_text(
        yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False)
    )
    print(args.output)


if __name__ == "__main__":
    main()
