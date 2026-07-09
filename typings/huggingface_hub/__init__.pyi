"""Minimal stubs for the slice of huggingface_hub smartpipe touches (models/local_ner)."""

def hf_hub_download(repo_id: str, filename: str) -> str: ...
def try_to_load_from_cache(
    repo_id: str, filename: str
) -> object: ...  # str when cached; sentinel/None otherwise
