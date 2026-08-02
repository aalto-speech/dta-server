"""Task catalogue: the app sends a task id, this resolves it to the prompt the model expects.

The `model_task_id` is the row index into the learned task embedding table. It is NOT
derivable at runtime — in the research repo it comes from enumerating every task id found
across every split, so adding one task renumbers all of them. It is frozen into
assets/tasks.json by scripts/dta_production/export_assets.py and must never be recomputed
on a server.
"""
import json
from dataclasses import dataclass

from .config import TASK_ID_MAP_JSON, TASKS_JSON


@dataclass(frozen=True)
class Task:
    task_id: str
    task_name: str
    model_task_id: int
    prompt_fi: str
    corpus: str
    n_train_recordings: int


class UnknownTask(KeyError):
    pass


class TaskCatalogue:
    def __init__(self, payload: dict):
        self._payload = payload
        self.prompt_version = payload["prompt_version"]
        self._tasks: dict[str, Task] = {}
        self._by_name: dict[str, str] = {}
        for tid, t in payload["tasks"].items():
            task = Task(task_id=tid, task_name=t["task_name"],
                        model_task_id=int(t["model_task_id"]), prompt_fi=t["prompt_fi"],
                        corpus=t["corpus"], n_train_recordings=int(t["n_train_recordings"]))
            self._tasks[tid] = task
            self._by_name[t["task_name"]] = tid
            for alt in t.get("alternate_task_names", []):
                self._by_name.setdefault(alt, tid)
        self._reserved = payload.get("reserved_task_ids_without_prompt", {})

    @classmethod
    def load(cls, path=TASKS_JSON, id_map_path=TASK_ID_MAP_JSON) -> "TaskCatalogue":
        cat = cls(json.loads(path.read_text()))
        # Integer task ids used by the calling application (dta-server's
        # assessments.task_id). Optional: absent file just means integers stay unresolvable.
        if id_map_path is not None and id_map_path.is_file():
            cat._int_map = {str(k): str(v)
                            for k, v in json.loads(id_map_path.read_text())["map"].items()}
        return cat

    def get(self, key) -> Task:
        """Resolve by task_id ('03_m'), task_name ('dta-task2_a'), or application integer.

        Integers resolve ONLY through assets/task_id_map.json. They are never passed to the
        model as-is: `model_task_id` is a row index into the learned task-embedding table, so
        an unmapped integer that silently indexed it would return a plausible wrong score.
        Unmapped integers raise instead.
        """
        k = str(key).strip()
        if k in self._tasks:
            return self._tasks[k]
        if k in self._by_name:
            return self._tasks[self._by_name[k]]
        if k in getattr(self, "_int_map", {}):
            return self._tasks[self._int_map[k]]
        if k.lstrip("-").isdigit():
            raise UnknownTask(
                f"integer task id {k!r} is not in assets/task_id_map.json. Add it there "
                f"rather than passing a task id through: the model's task embedding is "
                f"indexed by position, so a wrong id scores silently. "
                f"Mapped: {sorted(getattr(self, '_int_map', {}))}")
        if k in self._reserved:
            raise UnknownTask(
                f"task {k!r} has a reserved embedding slot but no prompt text in this "
                f"package; it was never part of the trained split")
        raise UnknownTask(f"unknown task {k!r}; known ids: {sorted(self._tasks)}")

    def dta_tasks(self) -> list[Task]:
        return [t for t in self._tasks.values() if t.corpus == "dta"]

    def all_tasks(self) -> list[Task]:
        return list(self._tasks.values())

    def __len__(self) -> int:
        return len(self._tasks)
