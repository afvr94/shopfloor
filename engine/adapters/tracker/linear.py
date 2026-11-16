from __future__ import annotations

from engine.adapters.tracker.base import Ticket, TrackerError, check_move, state_key
from engine.lib import config
from engine.lib.http import HttpError, request_json
from engine.lib.redact import redact

URL = "https://api.linear.app/graphql"

_ISSUE = """id identifier title description url createdAt
  state { name } assignee { name } labels { nodes { name } }
  relations { nodes { relatedIssue { identifier } } }"""

Q_ISSUES = """query($filter: IssueFilter, $after: String) {
  issues(filter: $filter, first: 50, after: $after, orderBy: createdAt) {
    nodes { %s } pageInfo { hasNextPage endCursor } } }""" % _ISSUE
Q_ISSUE = "query($id: String!) { issue(id: $id) { %s } }" % _ISSUE
Q_STATES = "query($filter: WorkflowStateFilter) { workflowStates(filter: $filter) { nodes { id } } }"
Q_VIEWER = "query { viewer { name } }"
# Small page sizes keep this under Linear's query complexity cap; labels may be team or workspace labels.
Q_TEAM = """query($key: String!, $label: String!) {
  teams(first: 1, filter: {key: {eq: $key}}) { nodes { key states(first: 100) { nodes { name } } } }
  issueLabels(first: 50, filter: {name: {eqIgnoreCase: $label}}) { nodes { name team { key } } } }"""
M_UPDATE = "mutation($id: String!, $stateId: String!) { issueUpdate(id: $id, input: {stateId: $stateId}) { success } }"
M_COMMENT = "mutation($input: CommentCreateInput!) { commentCreate(input: $input) { success comment { id } } }"
M_COMMENT_UPDATE = "mutation($id: String!, $body: String!) { commentUpdate(id: $id, input: {body: $body}) { success } }"
Q_ATTACHED = "query($id: String!) { issue(id: $id) { attachments(first: 50) { nodes { url } } } }"
M_LINK = "mutation($issueId: String!, $url: String!) { attachmentLinkURL(issueId: $issueId, url: $url) { success } }"


def _ticket(n: dict) -> Ticket:
    return Ticket(
        key=n["identifier"], id=n["id"], title=n["title"], body=n.get("description") or "",
        labels=[l["name"] for l in n["labels"]["nodes"]], state=n["state"]["name"], url=n["url"],
        assignee=(n.get("assignee") or {}).get("name"),
        relations=[r["relatedIssue"]["identifier"] for r in n.get("relations", {}).get("nodes", [])],
        created_at=n["createdAt"],
    )


class LinearTracker:
    def __init__(self, cfg: dict, post=None):
        self.cfg = cfg
        self.lin = cfg["tracker"]["linear"]
        self._key: str | None = None
        self.post = post or self._post

    def _post(self, query: str, variables: dict) -> dict:
        if self._key is None:
            self._key = config.read_secret(self.cfg["tracker"]["tokenFile"])
        extra = {"retries": 0} if query.lstrip().startswith("mutation") else {}  # never send a write twice
        try:
            resp = request_json("POST", URL, headers={"Authorization": self._key},
                                json_body={"query": query, "variables": variables}, **extra)
        except HttpError as e:
            raise TrackerError(redact(str(e))) from None
        if resp.get("errors"):
            raise TrackerError(redact("; ".join(str(e.get("message", e)) for e in resp["errors"])))
        return resp.get("data") or {}

    def list_candidates(self) -> list[Ticket]:
        lin = self.lin
        flt = {
            "team": {"key": {"eq": lin["team"]}},
            "labels": {"name": {"eqIgnoreCase": lin["label"]}},
            "state": {"name": {"eq": lin["states"]["todo"]}},
        }
        who = lin.get("assignee")
        if who == "me":
            flt["assignee"] = {"isMe": {"eq": True}}
        elif who:
            flt["assignee"] = {"email": {"eq": who}}
        hold = {h.lower() for h in lin.get("holdLabels", [])}
        out, after = [], None
        while True:
            issues = self.post(Q_ISSUES, {"filter": flt, "after": after})["issues"]
            out += [t for t in map(_ticket, issues["nodes"]) if not hold & {l.lower() for l in t.labels}]
            if not issues["pageInfo"]["hasNextPage"]:
                return sorted(out, key=lambda t: t.created_at)
            after = issues["pageInfo"]["endCursor"]

    def get(self, key: str) -> Ticket:
        node = self.post(Q_ISSUE, {"id": key}).get("issue")
        if not node:
            raise TrackerError(f"issue {key} not found")
        return _ticket(node)

    def _write(self, query: str, variables: dict, name: str) -> dict:
        res = self.post(query, variables).get(name)
        if not res or not res.get("success"):
            raise TrackerError(f"linear {name} did not succeed")
        return res

    def transition(self, key: str, to_key: str) -> None:
        if to_key not in self.lin["states"]:
            raise TrackerError(f"{to_key!r} is not a key of tracker.linear.states")
        t = self.get(key)
        labels = {l.lower() for l in t.labels}
        if self.lin["label"].lower() not in labels:
            raise TrackerError(f"{key} lacks the {self.lin['label']!r} label")
        if labels & {h.lower() for h in self.lin.get("holdLabels", [])}:
            raise TrackerError(f"{key} carries a hold label")
        frm = state_key(self.cfg, t.state)
        if frm is None:
            raise TrackerError(f"{key} is in state {t.state!r}, which maps to no tracker.linear.states key")
        if frm == to_key:  # a rebuild re-ships a ticket that is already there
            return
        check_move(self.cfg, frm, to_key)
        name = self.lin["states"][to_key]
        nodes = self.post(Q_STATES, {"filter": {"team": {"key": {"eq": self.lin["team"]}}, "name": {"eq": name}}})["workflowStates"]["nodes"]
        if not nodes:
            raise TrackerError(f"workflow state {name!r} not found in team {self.lin['team']}")
        self._write(M_UPDATE, {"id": t.id, "stateId": nodes[0]["id"]}, "issueUpdate")

    def comment(self, key: str, body: str) -> str:
        res = self._write(M_COMMENT, {"input": {"issueId": self.get(key).id, "body": body}}, "commentCreate")
        if not res.get("comment"):
            raise TrackerError("linear commentCreate returned no comment")
        return res["comment"]["id"]

    def update_comment(self, key: str, comment_id: str, body: str) -> None:
        self._write(M_COMMENT_UPDATE, {"id": comment_id, "body": body}, "commentUpdate")

    def link_pr(self, key: str, url: str) -> None:
        try:
            self._write(M_LINK, {"issueId": self.get(key).id, "url": url}, "attachmentLinkURL")
        except TrackerError:
            # Linear's GitHub integration often attaches the PR first, and linking the same URL again fails.
            node = self.post(Q_ATTACHED, {"id": key}).get("issue") or {}
            if url not in {a["url"] for a in (node.get("attachments") or {}).get("nodes", [])}:
                raise

    def viewer(self) -> str:
        return self.post(Q_VIEWER, {})["viewer"]["name"]

    def team_check(self) -> list[str]:
        """Config problems against the live team: missing team, opt-in label or workflow states."""
        team_key = self.lin["team"]
        data = self.post(Q_TEAM, {"key": team_key, "label": self.lin["label"]})
        nodes = data["teams"]["nodes"]
        if not nodes:
            return [f"team {team_key!r} not found"]
        usable = any(n["team"] is None or n["team"]["key"] == team_key for n in data["issueLabels"]["nodes"])
        states = {n["name"] for n in nodes[0]["states"]["nodes"]}
        problems = [] if usable else [f"label {self.lin['label']!r} not found in team {team_key}"]
        return problems + [f"workflow state {n!r} not found in team {team_key}"
                           for n in self.lin["states"].values() if n not in states]
