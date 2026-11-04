"""Fault conditions loaded from configs/faults.yaml."""

from pydantic import BaseModel

from ..tasks import faults_cfg


class FaultSpec(BaseModel):
    id: str
    type: str  # A1-p A1-i A2 A3 A4 B1 B2 B3 B4 BP CLEAN
    point: str  # R.start R.tool R.out P.out S.write E.tool E.args
    target: str = ""
    family: str = "clean"
    core: bool = False
    modes: list[str] = ["message", "state"]
    ext: bool = False  # extension beyond the 12 PLAN conditions

    @property
    def is_clean(self) -> bool:
        return self.type == "CLEAN"


def conditions(include_controls: bool = False) -> list[FaultSpec]:
    c = faults_cfg()
    out = [FaultSpec(**d) for d in c["conditions"]]
    if include_controls:
        out += [FaultSpec(**d) for d in c["controls"]]
    return out


def clean_point(point: str) -> FaultSpec:
    return FaultSpec(id=f"CLEAN_{point.replace('.', '')}", type="CLEAN", point=point)


def by_id(cid: str) -> FaultSpec:
    if cid.startswith("CLEAN_"):
        p = cid[len("CLEAN_") :]
        return clean_point(p[0] + "." + p[1:])
    for f in conditions(include_controls=True):
        if f.id == cid:
            return f
    raise KeyError(cid)
