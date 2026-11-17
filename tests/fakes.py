from __future__ import annotations


class FakeLinear:
    """Records (query, variables); answers from a list (popped in order) or a function(query, variables)."""

    def __init__(self, answers):
        self.answers = answers
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, query, variables):
        self.calls.append((query, variables))
        if callable(self.answers):
            return self.answers(query, variables)
        return self.answers.pop(0)


class FakeGh:
    """Records (argv, input); answers from a list (popped in order) or a function(argv, input).
    A ProcError answer is raised instead of returned."""

    def __init__(self, answers):
        self.answers = answers
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, argv, input=None):
        self.calls.append((argv, input))
        ans = self.answers(argv, input) if callable(self.answers) else self.answers.pop(0)
        if isinstance(ans, Exception):
            raise ans
        return ans


class FakeSlack:
    """Records (method, params); answers from a list (popped in order) or a function(method, params)."""

    def __init__(self, answers):
        self.answers = answers
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method, params):
        self.calls.append((method, params))
        if callable(self.answers):
            return self.answers(method, params)
        return self.answers.pop(0)
