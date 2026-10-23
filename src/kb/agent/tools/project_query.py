import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from rapidfuzz.fuzz import WRatio

LedgerField = Literal[
    "project_id", "project_name", "signed_date", "amount", "owner", "buyer", "supplier"
]


class ProjectArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_name: str | None = Field(None, description="项目名称关键词，不能填整句问题")
    fields: list[LedgerField] | None = None
    signed_after: date | None = Field(None, description="签订日期下限，含当天，YYYY-MM-DD")
    signed_before: date | None = Field(None, description="签订日期上限，含当天，YYYY-MM-DD")
    owner: str | None = Field(None, description="负责人姓名")
    min_amount: Decimal | None = Field(None, ge=0, description="合同金额下限，单位人民币元")
    max_amount: Decimal | None = Field(None, ge=0, description="合同金额上限，单位人民币元")
    amount_gt: Decimal | None = Field(
        None, ge=0, description="合同金额严格大于此值，人民币元；不加一"
    )
    amount_lt: Decimal | None = Field(
        None, ge=0, description="合同金额严格小于此值，人民币元；不减一"
    )

    @model_validator(mode="after")
    def valid_ranges(self):
        if self.signed_after and self.signed_before and self.signed_after > self.signed_before:
            raise ValueError("日期下限大于上限")
        if (
            self.min_amount is not None
            and self.max_amount is not None
            and self.min_amount > self.max_amount
        ):
            raise ValueError("金额下限大于上限")
        for lower, lower_open in [(self.min_amount, False), (self.amount_gt, True)]:
            for upper, upper_open in [(self.max_amount, False), (self.amount_lt, True)]:
                if (
                    lower is not None
                    and upper is not None
                    and (lower > upper or (lower == upper and (lower_open or upper_open)))
                ):
                    raise ValueError("金额筛选区间为空")
        return self


class Project(BaseModel):
    project_id: str
    project_name: str
    signed_date: date
    amount: Decimal = Field(ge=0)
    owner: str
    buyer: str | None = None
    supplier: str | None = None
    source_url: str | None = None
    amount_original: str | None = None


def read_ledger(path: Path) -> list[Project]:
    if not path.exists():
        raise FileNotFoundError(f"项目台账不存在: {path}")
    return [Project.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]


def query_projects(projects: list[Project], args: ProjectArgs, match_score: float) -> list[dict]:
    result = []
    for project in projects:
        if args.project_name and WRatio(args.project_name, project.project_name) < match_score:
            continue
        if args.owner and args.owner != project.owner:
            continue
        if args.signed_after and project.signed_date < args.signed_after:
            continue
        if args.signed_before and project.signed_date > args.signed_before:
            continue
        if args.min_amount is not None and project.amount < args.min_amount:
            continue
        if args.max_amount is not None and project.amount > args.max_amount:
            continue
        if args.amount_gt is not None and project.amount <= args.amount_gt:
            continue
        if args.amount_lt is not None and project.amount >= args.amount_lt:
            continue
        row = project.model_dump(mode="json", exclude_none=True)
        fields = args.fields or list(row)
        result.append(
            {k: v for k, v in row.items() if k in set(fields) | {"project_name", "source_url"}}
        )
    return result
