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
M_UPDATE = "mutation($id: String!, $stateId: String!) { issueUpdate(id: $id, input: {stateId: $stateId}) { success } }"
M_COMMENT = "mutation($input: CommentCreateInput!) { commentCreate(input: $input) { success comment { id } } }"
M_COMMENT_UPDATE = "mutation($id: String!, $body: String!) { commentUpdate(id: $id, input: {body: $body}) { success } }"
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
                return out
            after = issues["pageInfo"]["endCursor"]

    def get(self, key: str) -> Ticket:
        return _ticket(self.post(Q_ISSUE, {"id": key})["issue"])

    def transition(self, key: str, to_key: str) -> None:
        t = self.get(key)
        frm = state_key(self.cfg, t.state)
        if frm is None:
            raise TrackerError(f"{key} is in state {t.state!r}, which maps to no tracker.linear.states key")
        check_move(self.cfg, frm, to_key)
        name = self.lin["states"][to_key]
        nodes = self.post(Q_STATES, {"filter": {"team": {"key": {"eq": self.lin["team"]}}, "name": {"eq": name}}})["workflowStates"]["nodes"]
        if not nodes:
            raise TrackerError(f"workflow state {name!r} not found in team {self.lin['team']}")
        self.post(M_UPDATE, {"id": t.id, "stateId": nodes[0]["id"]})

    def comment(self, key: str, body: str) -> str:
        data = self.post(M_COMMENT, {"input": {"issueId": self.get(key).id, "body": body}})
        return data["commentCreate"]["comment"]["id"]

    def update_comment(self, key: str, comment_id: str, body: str) -> None:
        self.post(M_COMMENT_UPDATE, {"id": comment_id, "body": body})

    def link_pr(self, key: str, url: str) -> None:
        self.post(M_LINK, {"issueId": self.get(key).id, "url": url})

    def viewer(self) -> str:
        return self.post(Q_VIEWER, {})["viewer"]["name"]
