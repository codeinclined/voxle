# The running voxel edit session (an instance of edit.VOXEL_OT_edit) or None.
ACTIVE = None


def session_for(obj):
    """The active edit session if it is editing ``obj``."""
    s = ACTIVE
    if s is None:
        return None
    try:
        return s if s.obj == obj else None
    except ReferenceError:
        return None
