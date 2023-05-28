from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Iterable, TypedDict, Sequence, Optional
    from pathlib import Path

    class RecordEntry(TypedDict):
        id: str
        record: dict

    RecordList = dict[str, RecordEntry]

from itertools import chain
import operator
import json
from functools import reduce

from requests.exceptions import ConnectionError
from xdg_base_dirs import xdg_data_home

from .remote import KeyId, AliasedKeyId, get_remote_record
from .remote.error import NullRecordError, RemoteAccessError
from .bibtex import CAPTURED
from .term import TermWrite
from .session import CLISession
from .request import RemoteSession


def _get_record_lists(
    keyid_pairs: Iterable[KeyId], session: RemoteSession
) -> dict[KeyId, tuple[Optional[dict], list[tuple[str, str]]]]:
    """Given an iterable of KeyId, load all the associated records"""
    return {keyid: session.load_record(keyid) for keyid in keyid_pairs}


def _extract_keyid_pairs(
    to_resolve: Iterable[tuple[Optional[dict], list[tuple[str, str]]]]
) -> Sequence[KeyId]:
    return [
        KeyId.from_str(f"{key}:{identifier}")
        for _, related in to_resolve
        for key, identifier in related
    ]


def _resolve_all_records(
    keyid_pairs: Iterable[KeyId], resolved: set[KeyId], session: RemoteSession
) -> Iterable[tuple[KeyId, dict]]:
    # load all the records
    results = _get_record_lists(
        (keyid for keyid in keyid_pairs if keyid not in resolved), session
    )
    yield from ((keyid, rec) for keyid, (rec, _) in results.items() if rec is not None)

    resolved.update(keyid_pairs)
    to_resolve = _extract_keyid_pairs(results.values())

    if len(to_resolve) > 0:
        yield from _resolve_all_records(to_resolve, resolved, session)


def _get_record_dict(start_keyid: KeyId, session: CLISession) -> dict[KeyId, dict]:
    # if the keyid is already in the relations dictionary,
    # use those elements to build the record
    if start_keyid in session.relations:
        related_keys = session.relations[start_keyid]

        # sorting is not required since related_keys is already sorted
        candidates = {
            keyid: session.remote_session.load_record(keyid)[0]
            for keyid in related_keys
        }
        return {k: v for k, v in candidates.items() if v is not None}

    # otherwise, build the records and add them to the relations dict
    else:
        resolved_record_dict = dict(
            sorted(_resolve_all_records((start_keyid,), set(), session.remote_session))
        )
        session.relations.add(*resolved_record_dict.keys())
        return resolved_record_dict


class HoldRemoteRecord:
    """An unresolved record object which only resolves when you ask
    it for information that needs resolving.
    """

    def __init__(self, keyid: KeyId, session: CLISession):
        self.keyid = keyid
        self.session = session
        self.resolved: Optional[dict[KeyId, dict]] = None

    def resolve(self) -> dict[KeyId, dict]:
        if self.resolved is None:
            try:
                self.resolved = _get_record_dict(self.keyid, self.session)
            except ConnectionError:
                raise RemoteAccessError("no connection")
            if len(self.resolved.values()) == 0:
                raise NullRecordError(self.keyid)

        return self.resolved


class ArchiveRecord:
    def __init__(self, keyid: AliasedKeyId, session: CLISession):
        self.keyid = keyid
        self.cli_session = session
        self.record = HoldRemoteRecord(keyid.drop_alias(), self.cli_session)

    def __hash__(self) -> int:
        return hash(self.keyid)

    @classmethod
    def from_str(
        cls,
        keyid_str: str,
        session: CLISession,
        alias: Optional[str] = None,
    ):
        return cls(AliasedKeyId.from_str(keyid_str, alias=alias), session=session)

    def as_json(self) -> str:
        return json.dumps({str(k): v for k, v in self.record.resolve().items()})

    def as_joint_record(self) -> dict:
        records = list(reversed(self.record.resolve().values()))
        returned_record = reduce(operator.ior, records, {})

        # collect compound keys
        classifications = sorted(
            set(chain.from_iterable(rec.get("classifications", []) for rec in records))
        )
        if len(classifications) > 0:
            returned_record["classifications"] = classifications

        bibtex = reduce(operator.ior, (rec.get("bibtex", {}) for rec in records), {})
        if len(bibtex) > 0:
            returned_record["bibtex"] = bibtex

        return returned_record

    def is_null(self, warn: bool = False) -> bool:
        ret = len(self.as_joint_record()) == 0
        if warn and ret:
            TermWrite.warn(f"Null record '{self.keyid}'")
        return ret

    def related_keys(self) -> Iterable[KeyId]:
        try:
            return self.cli_session.relations.related(self.keyid)
        except KeyError:
            return self.record.resolve().keys()

    def priority_key(self) -> KeyId:
        try:
            return self.cli_session.relations.canonical(self.keyid)
        except KeyError:
            pass

        try:
            return next(iter(self.record.resolve().keys()))
        except StopIteration:
            raise NullRecordError(self.keyid)

    def get_local_bibtex(self) -> dict:
        return reduce(
            operator.ior,
            (keyid.toml_record() for keyid in reversed(self.record.resolve().keys())),
            {},
        )

    def as_bibtex(self) -> dict:
        joint_record = self.as_joint_record()

        eprint_keyid = self.priority_key()
        eprint = {
            "eprint": eprint_keyid.identifier,
            "eprinttype": str(eprint_keyid.key),
        }

        captured = {k: v for k, v in joint_record.items() if k in CAPTURED}

        special = {
            "ID": str(self.keyid) if self.keyid.alias is None else self.keyid.alias,
            "ENTRYTYPE": joint_record["bibtype"],
        }
        if "authors" in joint_record.keys():
            special["author"] = " and ".join(joint_record["authors"])

        return {**eprint, **captured, **special, **self.get_local_bibtex()}

    def as_tuple(self) -> tuple[str, str, str | None, str | None, str | None, bool]:
        bibtex_record = self.as_bibtex()
        return (
            bibtex_record["eprinttype"],
            bibtex_record["eprint"],
            bibtex_record.get("title"),
            bibtex_record.get("author"),
            bibtex_record.get("year"),
            (
                xdg_data_home()
                / "mathbib"
                / "files"
                / "zbl"
                / f"{bibtex_record['eprint']}.pdf"
            ).exists(),
        )

    def show_url(self) -> Optional[str]:
        for keyid in self.related_keys():
            show_url = get_remote_record(keyid).show_url
            if show_url is not None:
                return show_url(keyid.identifier)
        return None

    def related_file(self) -> Optional[Path]:
        if self.keyid.file_path().exists():
            return self.keyid.file_path()

        for keyid in self.related_keys():
            if keyid.file_path().exists():
                return keyid.file_path()
        return None

    def download_file(self) -> Optional[Path]:
        for keyid in self.related_keys():
            download_url = get_remote_record(keyid).download_url
            path = keyid.file_path()
            if (
                download_url is not None
                and self.cli_session.remote_session.make_raw_streaming_request(
                    download_url(keyid.identifier), path
                )
            ):
                return path
        return None

    def __str__(self) -> str:
        key, id, title, author, year, exists = self.as_tuple()
        return f"{key}:{id} || {title} || {author} || {year} || file:{exists}"
