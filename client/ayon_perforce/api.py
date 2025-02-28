from pathlib import Path


class P4_Workspace:
    def __init__(self, name: str):
        self.name = name
        self._workspace_dir = Path(f"C:/Perforce/{name}")

    def checkout_stream(self, stream: str):
        pass

    def revert_to_changelist(self, changelist: int):
        pass
