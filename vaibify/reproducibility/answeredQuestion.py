"""One representation for a question whose DECLINING answer passes.

Three requirements on the ladder ask the researcher something rather
than demanding an artifact, and all three pass on any answer: Personal
AI Configuration (``none`` / ``declared-private`` / ``included``), each
of the three determinism questions ("I do not accept last-digit
differences" is a complete answer), and the environment archive
("declined" is a complete answer). ANSWERING is the criterion; only
silence fails.

Two properties are load-bearing and were re-derived independently in
each of the first two, which is what makes this the rule-of-three
extraction rather than a tidy-up:

- **The choice is its own key.** A VALUE cannot express consideration.
  ``bAcceptBlasVariance: false`` is what the old determinism form wrote
  when submitted with nothing ticked, so it means "unanswered" and
  "declined" at once, and reading it as a deliberate "no" attests a
  claim the researcher never made.
- **An answer naming a value must CARRY that value.** "The thread count
  is pinned" with no count is half an answer, and a rerun could not act
  on it. Which answers need one is a property of the question, so it
  travels in the question rather than in each gate.

A question is a plain dict so it can be shipped to the browser as-is::

    {
        "sKey":                  stable identifier, used in issue lists
        "sAnswerKey":            where the CHOICE is recorded
        "tAnswers":              the closed set of valid choices
        "sValueKey":             where a choice's VALUE is recorded, "" for none
        "tAnswersNeedingValue":  the choices that are incomplete without it
        "sLabel":                researcher-facing name
        "sPlainQuestion":        researcher-facing question
    }

``sLabel`` and ``sPlainQuestion`` never carry a schema key: a
researcher is being asked about their science, not about JSON.
"""

__all__ = [
    "fbQuestionIsAnswered",
    "flistDescribeUnansweredQuestions",
    "flistSelectUnansweredKeys",
]


def fbQuestionIsAnswered(dictAnswers, dictQuestion):
    """Return True iff one question carries a recorded, valid answer."""
    if not isinstance(dictAnswers, dict):
        return False
    sAnswer = dictAnswers.get(dictQuestion["sAnswerKey"])
    if sAnswer not in dictQuestion["tAnswers"]:
        return False
    if sAnswer not in dictQuestion.get("tAnswersNeedingValue", ()):
        return True
    return _fbValueIsPresent(dictAnswers.get(dictQuestion["sValueKey"]))


def _fbValueIsPresent(jsonValue):
    """Return True iff a value an answer names was actually supplied."""
    if jsonValue is None:
        return False
    if isinstance(jsonValue, str):
        return jsonValue.strip() != ""
    return True


def flistSelectUnansweredKeys(dictAnswers, tQuestions):
    """Return the ``sKey`` of every question still unanswered."""
    return [
        dictQuestion["sKey"]
        for dictQuestion in tQuestions
        if not fbQuestionIsAnswered(dictAnswers, dictQuestion)
    ]


def flistDescribeUnansweredQuestions(dictAnswers, tQuestions):
    """Return one researcher-facing issue line per unanswered question.

    Named individually because they are separate questions with
    separate answers; one "not declared" line cannot tell a researcher
    which is still open, and the rows that report them carry a marker
    each.
    """
    return [
        dictQuestion["sLabel"] + " — not answered yet. "
        + dictQuestion["sPlainQuestion"]
        for dictQuestion in tQuestions
        if not fbQuestionIsAnswered(dictAnswers, dictQuestion)
    ]
