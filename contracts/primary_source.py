# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import hashlib
import json
import datetime
from dataclasses import dataclass
from genlayer import *


@allow_storage
@dataclass
class Edition:
    digest: str            # sha256 hex of the raw body
    url: str               # source URL that was captured
    status: str            # OK | NOT_FOUND | UNREACHABLE
    captured_at: str       # ISO timestamp pinned to the transaction
    captured_by: str       # address that triggered the capture (hex)
    body_len: u32          # length of the fetched body
    divergent: bool        # True if a later edition saw a different digest


@allow_storage
@dataclass
class Evidence:
    evidence_id: str       # canonical id (lowercased URL)
    url: str               # original URL as first supplied
    editions: DynArray[Edition]


class PrimarySource(gl.Contract):
    """
    Evidence Provenance Conservatory (MVP).

    Attests THAT a source said something at a time — never THAT it is true.
    Capture is the only consensus step: leader and validators each fetch the
    URL and agree on an objective provenance tuple (status class + digest).
    Storage, editions and lookups are fully deterministic.
    """

    # evidence_id -> evidence record with its immutable edition list
    evidence: TreeMap[str, Evidence]

    def __init__(self):
        pass

    # ---------- internals ----------

    def _canonical_id(self, url: str) -> str:
        """Standards-correct URL canonicalization (RFC 3986).

        Normalizes only case-insensitive components (scheme, host, default
        port) and drops the fragment. Path and query are preserved exactly as
        supplied, since they are case-sensitive and identify distinct resources.
        """
        url = url.strip()
        if '://' in url:
            scheme, rest = url.split('://', 1)
            scheme = scheme.lower()
        else:
            scheme, rest = 'https', url

        # Split authority from path (path includes query if present).
        slash_idx = rest.find('/')
        if slash_idx == -1:
            authority, path = rest, ''
        else:
            authority, path = rest[:slash_idx], rest[slash_idx:]

        # Host is case-insensitive; strip default ports.
        if ':' in authority:
            host, port = authority.rsplit(':', 1)
            host = host.lower()
            if (scheme == 'https' and port == '443') or (scheme == 'http' and port == '80'):
                authority = host
            else:
                authority = f'{host}:{port}'
        else:
            authority = authority.lower()

        canonical = f'{scheme}://{authority}{path}'
        # Fragment is never sent to the server; drop it for identity.
        if '#' in canonical:
            canonical = canonical.split('#', 1)[0]
        return canonical

    def _status_class(self, http_status: int) -> str:
        if 200 <= http_status < 300:
            return 'OK'
        if http_status == 404:
            return 'NOT_FOUND'
        if http_status == 410:
            return 'GONE'
        return 'OTHER'

    def _to_bytes(self, body) -> bytes:
        if isinstance(body, str):
            return body.encode('utf-8')
        return bytes(body)

    # ---------- consensus: the capture itself ----------

    @gl.public.write
    def capture_or_recapture(self, url: str) -> str:
        """
        Capture `url` (or add edition N+1 when it already exists).
        Returns the evidence_id.
        """
        eid = self._canonical_id(url)
        def leader_fetch() -> str:
            # SHA-256 is available in the GenVM environment.
            try:
                web = gl.nondet.web.get(url)
                body = self._to_bytes(web.body)
                digest = hashlib.sha256(body).hexdigest()
                status = int(web.status)
                body_len = len(body)
            except Exception:
                return json.dumps(_err('UNREACHABLE', url))
            return json.dumps({
                'digest': digest,
                'status': self._status_class(status),
                'body_len': body_len,
                'url': url,          # exact resource fetched
            })

        def validator_fetch(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            leader_data = json.loads(leader_res.calldata)
            try:
                mine = json.loads(leader_fetch())
            except Exception:
                return False
            if leader_data.get('status') == 'UNREACHABLE':
                # agree only if the validator independently could not fetch
                return mine.get('status') == 'UNREACHABLE'
            if mine.get('status') == 'UNREACHABLE':
                return False
            # Objective tuple only: identical digest + same status class.
            # Body length is informational, not consensus-critical.
            return (
                mine.get('digest') == leader_data.get('digest')
                and mine.get('status') == leader_data.get('status')
            )

        raw = gl.vm.run_nondet_unsafe(leader_fetch, validator_fetch)
        cap = json.loads(raw)

        had_record = eid in self.evidence
        if not had_record:
            self.evidence[eid] = Evidence(
                evidence_id=eid,
                url=url,
                editions=[],
            )
        # always operate on the storage view, never on the plain object
        # (docs' storage gotcha: assignment stores a copy)
        ev = self.evidence[eid]

        if cap['status'] == 'UNREACHABLE' and had_record:
            # a flaky network must not pollute an existing record
            raise gl.vm.UserError('capture UNREACHABLE; existing editions untouched')

        prior_editions = len(ev.editions)
        if prior_editions > 0:
            last = ev.editions[prior_editions - 1]
            if last.digest != cap['digest'] and last.status == 'OK' and cap['status'] == 'OK':
                # silent edit detected -> mark the older edition divergent.
                # editions themselves are never mutated beyond this flag.
                last.divergent = True
                ev.editions[prior_editions - 1] = last  # persist flag via storage view

        ev.editions.append(Edition(
            digest=cap['digest'],
            url=cap.get('url', ev.url),
            status=cap['status'],
            captured_at=datetime.datetime.now().isoformat(),
            captured_by=gl.message.sender_address.as_hex,
            body_len=cap.get('body_len', 0),
            divergent=False,
        ))
        return eid

    # ---------- views ----------

    @gl.public.view
    def get_edition(self, evidence_id: str, index: int) -> dict:
        ev = self.evidence.get(self._canonical_id(evidence_id))
        if ev is None:
            raise gl.vm.UserError('no such evidence')
        if index >= len(ev.editions):
            raise gl.vm.UserError('no such edition')
        e = ev.editions[index]
        return {
            'evidence_id': ev.evidence_id,
            'url': ev.url,
            'edition': index,
            'digest': e.digest,
            'status': e.status,
            'captured_at': e.captured_at,
            'captured_by': e.captured_by,
            'body_len': e.body_len,
            'divergent': e.divergent,
        }

    @gl.public.view
    def get_latest_edition(self, evidence_id: str) -> dict:
        ev = self.evidence.get(self._canonical_id(evidence_id))
        if ev is None or len(ev.editions) == 0:
            raise gl.vm.UserError('no such evidence')
        return self.get_edition(evidence_id, len(ev.editions) - 1)

    @gl.public.view
    def edition_count(self, evidence_id: str) -> int:
        ev = self.evidence.get(self._canonical_id(evidence_id))
        if ev is None:
            return 0
        return len(ev.editions)

    @gl.public.view
    def has_diverged(self, evidence_id: str) -> bool:
        """True if any capture of this evidence ever disagreed with a newer one."""
        ev = self.evidence.get(self._canonical_id(evidence_id))
        if ev is None:
            return False
        for e in ev.editions:
            if e.divergent:
                return True
        return False


def _err(status: str, url: str = '') -> dict:
    return {'digest': '', 'status': status, 'body_len': 0, 'url': url}
