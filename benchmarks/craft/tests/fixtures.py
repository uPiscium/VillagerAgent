import json


def write_minimal_structures_dataset(path, *, count: int = 2):
    path.parent.mkdir(parents=True, exist_ok=True)
    dataset = [
        {
            "id": f"test_structure_{index}",
            "structure": {},
            "spans": {},
            "director_views": {"D1": {}, "D2": {}, "D3": {}},
        }
        for index in range(count)
    ]
    path.write_text(json.dumps(dataset), encoding="utf-8")
    return path
