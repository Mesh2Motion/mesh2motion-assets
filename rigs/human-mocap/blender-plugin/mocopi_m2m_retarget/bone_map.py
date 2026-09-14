"""Loading of the explicit Mocopi -> Mesh2Motion bone map."""

import json
import os

from .retarget_engine import BonePair

DEFAULT_OPTIONS = {
    "auto_scale": True,
    "use_pose": "REST",
}


class BoneMapError(Exception):
    pass


def load(path):
    """Read a bone map JSON file.

    Returns (pairs, options). Raises BoneMapError with something a user can
    act on -- this file is meant to be hand edited, so bad input is expected.
    """
    if not os.path.isfile(path):
        raise BoneMapError("Bone map not found at {}".format(path))

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise BoneMapError("Bone map is not valid JSON: {}".format(exc))

    if not isinstance(data, dict) or "bones" not in data:
        raise BoneMapError("Bone map has no 'bones' list")

    entries = data["bones"]
    if not isinstance(entries, list):
        raise BoneMapError("'bones' must be a list")

    pairs = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise BoneMapError("Entry {} is not an object".format(index))

        source = entry.get("source")
        target = entry.get("target")

        if not source or not target:
            raise BoneMapError(
                "Entry {} needs both 'source' and 'target'".format(index)
            )

        copy_location = entry.get("copy_location")
        if copy_location is not None and not isinstance(copy_location, bool):
            raise BoneMapError(
                "Entry {} ('{}'): 'copy_location' must be true or false".format(index, source)
            )

        pairs.append(BonePair(source, target, copy_location))

    if not pairs:
        raise BoneMapError("Bone map is empty")

    options = dict(DEFAULT_OPTIONS)
    file_options = data.get("options") or {}
    if not isinstance(file_options, dict):
        raise BoneMapError("'options' must be an object")

    options.update(
        {key: value for key, value in file_options.items() if key in DEFAULT_OPTIONS}
    )

    if options["use_pose"] not in ("REST", "CURRENT"):
        raise BoneMapError("'use_pose' must be \"REST\" or \"CURRENT\"")

    return pairs, options
