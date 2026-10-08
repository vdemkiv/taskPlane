"""Run-bound, observed authorization for optional automatic phase decisions.

This is cooperative workflow policy, not host authentication or tool permission.
No policy executes commands, approves missing evidence, or extends a route.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from . import workflow as w
from .primitives import content_fingerprint

SCHEMA = "taskplane.approval-policy/v1"
ASSESSMENT = "taskplane.policy-assessment/v1"
ASSESSMENT_LIMIT = 64 * 1024


def read_assessment(workspace: Path, filename: str = "", inline: str | None = None) -> dict[str, Any]:
    """Accept a bounded control payload without permitting sealed workspace writes."""
    from . import workflow_evidence as evidence
    import os
    import stat
    w.require(bool(filename) != (inline is not None), "invalid_evidence",
              "Supply exactly one assessment file or inline JSON object.")
    if inline is not None:
        raw = inline.encode("utf-8")
    else:
        target = evidence.path(workspace, filename)
        try:
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                w.require(stat.S_ISREG(info.st_mode) and info.st_size <= ASSESSMENT_LIMIT,
                          "invalid_evidence", "Assessment must be a regular file of at most 64 KiB.")
                raw = stream.read(ASSESSMENT_LIMIT + 1)
        except OSError as exc:
            raise w.Refusal("invalid_evidence", f"Assessment unavailable: {filename}") from exc
    w.require(len(raw) <= ASSESSMENT_LIMIT, "invalid_evidence", "Assessment exceeds 64 KiB.")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise w.Refusal("invalid_evidence", "Assessment must be a JSON object.") from None
    w.require(isinstance(value, dict), "invalid_evidence", "Assessment must be a JSON object.")
    return dict(value)


def policy_binding(state: dict[str, Any]) -> dict[str, Any]:
    return {**{k: state[k] for k in ("workspace", "root", "run", "revision")},
            "scope_digest": content_fingerprint(state["scope"])}


def _text(value: Any, maximum: int = 4096) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def decision_text(excerpt: str) -> str:
    """Remove quoted examples and normalize presentation, not user provenance."""
    text = excerpt.casefold().replace("’", "'")
    text = re.sub(r'```[\s\S]*?```|`[^`]*`|“[^”]*”|"[^"]*"|(?<!\w)\'[^\'\n]+\'(?!\w)', ' ', text)
    return ' '.join(line.strip() for line in text.splitlines() if not line.lstrip().startswith('>'))


def decision_phases(excerpt: str) -> set[str]:
    """Keep every named approval target; a recorder cannot discard a second one."""
    phases = '|'.join(w.PHASES)
    text = decision_text(excerpt)
    verbs = r'(?:approv(?:e|ed)|accept(?:ed)?|confirm(?:ed)?|authori[sz](?:e|ed)|consent(?:ed)?|assent(?:ed)?|apparoved|apprvoed|sign(?:ed)? off(?: on)?|(?:happy|satisfied) with)'
    active = r'\b' + verbs + r'\s+(?:(?:the|this|that)\s+)?(?:current\s+)?(' + phases + r')\b'
    passive = r'\b(' + phases + r')\s+(?:phase\s+)?(?:is\s+)?(?:approved|accepted|confirmed|authorized|authorised|signed off)\b'
    signed = r'\bsign(?:ed)?\s+(?:(?:the|this|that)\s+)?(?:current\s+)?(' + phases + r')\s+(?:phase\s+)?off\b'
    return set(re.findall(active, text)) | set(re.findall(passive, text)) | set(re.findall(signed, text))


def _complete_approval_response(excerpt: str) -> bool:
    """Positively classify every clause, including quoted and unknown content.

    Classify the complete response compositionally, with at least one unquoted
    current grant. Supporting explanations and courtesy phrases are not grants.
    Unknown or qualified prose still needs clarification; a recorder's chosen
    label cannot erase it. Use the same grant grammar throughout, rather than a
    separate opening-word allowlist that rejects valid passive or signed consent.
    """
    phase = r'(?:' + '|'.join(w.PHASES) + r')'
    target = r'(?:' + phase + r'(?:\s+phase)?|phase|checkpoint|output|proposal|changes?|repairs?|work)'
    obj = (r'(?:it|this|that|(?:(?:the|this|that)\s+)?(?:current\s+)?' + target
           + r'|(?:checkpoint\s+)?[0-9a-f]{32})')
    actor = r'(?:(?:i|we)\s+(?:have\s+)?)?'
    adverb = r'(?:(?:hereby|explicitly|now)\s+)?'
    grant_verb = (r'(?:approv(?:e|ed)|accept(?:ed)?|confirm(?:ed)?|authori[sz](?:e|ed)|'
                  r'consent(?:ed)?|assent(?:ed)?|apparoved|apprvoed)')
    active = actor + adverb + grant_verb + r'(?:\s+' + obj + r')?(?:\s+as (?:is|presented))?'
    signed = (actor + adverb + r'sign(?:ed)?\s+(?:off(?:\s+(?:on\s+)?' + obj + r')?|'
              + obj + r'\s+off)')
    passive = obj + r'\s+(?:is\s+)?(?:approved|accepted|confirmed|authorized|authorised|signed\s+off)'
    noun = (r'(?:approvals?|confirmations?|authori[sz]ations?|consents?|acceptances?|'
            r'permissions?|assents?|clearances?|sign\s*offs?|go\s+ahead|green\s+light|ok(?:ay)?)')
    subject = r'(?:(?:my|our|the)\s+)?' + noun
    given = (subject + r'\s+(?:(?:is|are|has been|have been)\s+)?(?:given|granted|confirmed)|'
             + actor + adverb + r'(?:give|given|grant(?:ed)?|provide(?:d)?)\s+' + subject + r'|'
             r'(?:i am|we are)\s+(?:giving|granting|providing)\s+' + subject + r'|'
             r'you\s+have\s+(?:my|our)\s+' + noun)
    present = r'(?:' + '|'.join((active, signed, passive, given)) + r')(?:\s+(?:now|today))?'
    assent = (r'(?:ok(?:ay)?|yes|yep|yeah|lgtm|go ahead|proceed|continue|ship it|'
              r'(?:looks?|sounds?) (?:good|great)(?: to (?:me|us))?|'
              r'(?:this|that|it) works for (?:me|us)|'
              r'(?:i am|we are) (?:happy|satisfied) with\s+' + obj + r')')
    item = r'(?:' + present + '|' + assent + r')'
    item = r'(?:please\s+)?' + item
    grant = item + r'(?:\s+(?:and\s+)?' + item + r')*'
    courtesy = r'(?:thanks|thank you)(?: very much)?'
    grant = r'(?:(?:' + courtesy + r')\s+)?' + grant + r'(?:\s+(?:please|' + courtesy + r'))?'

    requester = r'(?:i|we|you|(?:(?:the|my|our|your)\s+)?(?:reviewer|approver|owner|team))'
    request_adverb = r'(?:(?:still|also|really|just)\s+){0,2}'
    request_verb = r'(?:need(?:s|ed|ing)?|requir(?:e[sd]?|ing)|want(?:s|ed|ing)?)'
    request = (requester + r'\s+' + request_adverb
               + r'(?:(?:am|are|is|was|were|will|shall|do|does)\s+)?'
               + request_adverb + request_verb + r'|(?:give|allow)\s+(?:me|us)|let\s+(?:me|us)\s+have')
    work_quantity = r'(?:(?:some|a little)\s+)?(?:(?:more|additional|extra)\s+)?time'
    work_object = (r'(?:(?:the|this|that|these|those|our)\s+)?(?:current\s+)?'
                   r'(?:plan|implementation|changes|feature|release|code|documentation)')
    work_action = r'(?:implement|build|publish|ship)\s+' + work_object
    work_purpose = (r'(?:to\s+' + work_action
                    + r'(?:\s+and\s+(?:to\s+)?' + work_action + r'){0,3}'
                    + r'|for\s+(?:implementation|publication)\s+of\s+' + work_object + r')')
    work_time = (r'(?:' + request + r')\s+' + work_quantity + r'\s+' + work_purpose
                 + r'(?:\s+(?:today|tomorrow|please))?')
    work = (r'(?:i|we)\s+will\s+(?:' + work_action + r'|publish|ship)'
            + r'(?:\s+(?:today|tomorrow))?')
    explanation = (
        r'(?:all\s+)?(?:required\s+)?(?:tests|checks)\s+passed',
        r'outstanding work',
        r'(?:the\s+)?results\s+(?:look okay|are ready)',
        r'(?:the\s+)?results\s+(?:clearly\s+)?show\s+(?:over\s+)?[0-9]+%\s+reduction',
        r'(?:this|that|it) meets (?:the|our) requirements',
        r'everything checks out',
        courtesy,
    )
    complete = '(?:' + '|'.join((grant, work_time, work, *explanation)) + ')'
    # Retain quoted contents, remove only presentation delimiters. Unrecognized
    # symbols/words remain in the clause and fail the complete match. Apostrophes
    # within words are retained, so unsupported contractions cannot be erased.
    def clauses(text: str) -> list[str]:
        text = text.casefold().replace("’", "'")
        text = re.sub(r'["“”`]|(?<!\w)\'|\'(?!\w)|(?m:^\s*>\s?)', ' ', text)
        text = re.sub(r'(?<=\w)[-\u2010\u2011](?=\w)', ' ', text)
        return [words for part in re.split(r'[.!;\n:—–]+', text)
                if (words := re.sub(r'\s+', ' ', part.replace(',', ' ')).strip())]

    return (all(re.fullmatch(complete, part) for part in clauses(excerpt))
            and any(re.fullmatch(grant, part) for part in clauses(decision_text(excerpt))))


def conversational_choice(excerpt: str) -> str | None:
    """Recognize explicit everyday decisions; ambiguity requires clarification.

    This only classifies an observed response. The adapter still verifies its
    human source, the presented checkpoint, ordering and exact binding.
    """
    text = re.sub(r'\s+', ' ', decision_text(excerpt)).strip()
    qualifiers = re.sub(r'\s+', ' ', excerpt.casefold().replace("’", "'")).strip()
    # A punctuation boundary must not hide a condition on any decision, including
    # dissent. Negative implementation requirements are handled separately below.
    if not text or '?' in qualifiers or re.search(
        r"\b(?:if|unless|until|when|after|before|once|provided|assuming|hypothetically|example|would|might|maybe|perhaps|subject to|as long as)\b", qualifiers
    ):
        return None
    lead = re.sub(r'^(?:please\s+)?(?:i\s+)?', '', text).rstrip('.! ')
    dissent = {
        'cancelled': r'(?:cancel(?:led)?|stop|abort|(?:withdraw|revoke)(?:\s+(?:my|our|the))?\s+(?:approval|consent))',
        'rejected': r'(?:reject(?:ed)?|decline(?:d)?)',
        'changes_requested': r'(?:changes? requested|request changes|changes?(?=\s*:)|needs? (?:changes|revisions)|fix (?:it all|(?:the )?issues)|revise)',
    }
    # A direct request for correction stays dissent when its explanation contains
    # negative requirements. Positive-consent restrictions must not erase it.
    # Conditions on the decision itself and mixed decisions still need clarification.
    current_target = r'(?:(?:this|the)\s+)?(?:current\s+)?(?:workflow|run|checkpoint|output)'
    dissent_target = r'(?:(?:for\s+)?' + current_target + r'|here|it)?'
    # An explicit workflow target remains a decision even with trailing prose.
    # A bare verb needs a boundary or a modifier, not an implementation object.
    decision_target = (r'(?:\s+(?:' + current_target + r'|here|it)\b|\s*$|'
                       r'\s+(?=please\b|now\b|yet\b|again\b|anymore\b|today\b|tomorrow\b|'
                       r'for\b|at\b|as\b|right\b|just\b|[a-z]+ly\b))')
    direct_dissent = {
        choice: r'(?:please\s+)?(?:i\s+)?' + pattern + r'\b' + decision_target
        for choice, pattern in dissent.items()
    }
    assent_target = (r'(?:(?:the|this|that)\s+)?(?:current\s+)?(?:'
                     + '|'.join(w.PHASES) + r'|phase|checkpoint|output|proposal|changes?|repairs?|work)')
    targeted_assent = (r'\b(?:approv(?:e|ed)|accept(?:ed)?|confirm(?:ed)?|authori[sz](?:e|ed)|'
                       r'consent(?:ed)?|assent(?:ed)?|sign(?:ed)? off)\s+'
                       r'(?:' + assent_target + r'|it|this|that)\b')
    for choice, pattern in dissent.items():
        matched = re.match(r'^' + pattern + r'\b', lead)
        if matched:
            tail = lead[matched.end():]
            decision_tail = re.split(r'[:.!;\n]', tail, maxsplit=1)[0].strip()
            # Consume the decision clause. "Cancel nothing" and conditional
            # requests must not be mistaken for a direct cancellation/change.
            if not re.fullmatch(dissent_target, decision_tail):
                return None
            for clause in re.split(r'[.!;:\n—–]+|,|\b(?:then|but|and)\b', qualifiers):
                # Retain quoted qualifications instead of deleting their content.
                clause = clause.strip(' \t\'"`“”')
                if (re.fullmatch(r'(?:no|nope)', clause)
                        or re.search(r'\b(?:never mind|on second thought|i changed my mind)\b', clause)):
                    return None
                if any(other != choice and re.search(r'\b' + direct, clause)
                       for other, direct in direct_dissent.items()):
                    return None
                # Introductory prose must not conceal a complete conflicting or
                # retracted decision. Keep the target bounded so implementation
                # requirements such as "never cancel pending payments" survive.
                if any(re.search(r"\b(?:not|never|cannot|no longer|\w+n't)\b.*?\b"
                                 + direct, clause) for direct in direct_dissent.values()):
                    return None
                if re.search(targeted_assent, clause):
                    return None
                # Descriptive prohibitions ("never accept invalid IDs") are
                # not positive decisions. A separate complete assent is mixed.
                if _complete_approval_response(clause):
                    return None
            return choice
    # A later qualification/negation must not be hidden by an affirmative prefix.
    if re.search(r"\b(?:but|however|except|yet|no|not|never|don't|cannot|can't|shouldn't|without)\b", qualifiers):
        return None
    choices = set()
    if _complete_approval_response(excerpt):
        choices.add('approved')
    # Every recognized dissent form also qualifies an affirmative prefix.
    # Quoting that qualification cannot hide it from the mixed-decision check.
    conflict = any(re.search(r'\b' + pattern + r'\b', qualifiers) for pattern in dissent.values())
    if 'approved' in choices and conflict:
        return None
    # "Yes, explain ..." or "continue reviewing" is not acceptance of an output.
    if 'approved' in choices and re.search(
        r'\b(?:explain|review(?:ing)?|investigat\w*|research|discuss|consider|approved by|implement an? option|add an? (?:option|feature))\b', text
    ):
        return None
    return next(iter(choices)) if len(choices) == 1 else None


def _unnecessary_decision(predicate: str) -> bool:
    """Only a local, unambiguous negation removes a human dependency."""
    for contraction, expanded in (("won't", 'will not'), ("can't", 'can not'), ("shan't", 'shall not')):
        predicate = predicate.replace(contraction, expanded)
    predicate = re.sub(r"\b(\w+)n't\b", r'\1 not', predicate)
    words = predicate.split()
    certain = {'is', 'are', 'was', 'were', 'be', 'been', 'being', 'remain', 'remains',
               'will', 'would', 'shall', 'should', 'must', 'can', 'could', 'have', 'has',
               'had', 'do', 'does', 'did', 'need', 'needs', 'to', 'required', 'needed',
               'necessary', 'mandatory', 'unnecessary', 'optional', 'still', 'also',
               'explicitly', 'manually', 'personally', 'strictly'}
    negatives = [i for i, word in enumerate(words) if word in ('not', 'never', 'no')]
    if words[-1] in ('unnecessary', 'optional'):
        return not negatives and all(word in certain for word in words)
    if len(negatives) != 1:
        return False
    index = negatives[0]
    if words[index] == 'no':
        if words[index:index + 2] != ['no', 'longer']:
            return False
        end = index + 2
    else:
        end = index + 1
    # "Not only required", "not always required" and "not yet required"
    # retain a possible human checkpoint; none means explicitly unnecessary.
    return (all(word in certain | {'always'} for word in words[:index])
            and all(word in certain for word in words[end:]))


def _unnecessary_clause(clause: str) -> bool:
    """Recognize an entire explicitly unnecessary human-decision clause.

    The grammar consumes the subject and every predicate. An unknown qualifier
    cannot be discarded by matching a shorter unnecessary prefix.
    """
    signoff = r'sign(?:s|ed|ing)?[\s\-\u2010\u2011]?offs?'
    decision_verb = (r'(?:approv(?:e[sd]?|ing)|confirm(?:s|ed|ing)?|'
                     r'authori[sz](?:e[sd]?|ing)|consent(?:s|ed|ing)?|accept(?:s|ed|ing)?|'
                     r'assent(?:s|ed|ing)?|' + signoff + r')')
    decision_noun = (r'(?:approvals?|confirmations?|authori[sz]ations?|consents?|'
                     r'acceptances?|permissions?|assents?|clearances?|' + signoff + r')')
    conjunction = r'(?:\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)'
    nouns = decision_noun + r'(?:' + conjunction + decision_noun + r'){0,3}'
    modifier = (r'(?!(?:and|or|but|while|although|from|by|is|are|be|will|must|not|no|'
                r'required|needed|necessary|mandatory)\b)[a-z][a-z-]{0,31}')
    role = (r'(?:(?:the|a|an|my|our|your)\s+)?(?:' + modifier + r'\s+){0,3}'
            r'(?:humans?|users?|reviewers?|owners?|approvers?|maintainers?|managers?|operators?)')
    human_actor = r'(?:i|we|you|' + role + r')'
    human_source = r'(?:me|us|you|' + role + r')'
    human_noun = (r'(?:(?:my|our|your|human|user|manual|' + role + r"(?:'s|')?)\s+"
                  + nouns + r'|' + nouns + r'\s+(?:from|by)\s+' + human_source + r')')
    human_subject = ('(?:' + human_noun + '|' + nouns + r')(?:' + conjunction +
                     r'(?:' + human_noun + '|' + nouns + r')){0,3}')
    requirement = r'(?:required|needed|necessary|mandatory|unnecessary|optional)'
    adverb = r'(?:still|also|always|only|just|yet|ever|sometimes|[a-z]{2,24}ly)'
    auxiliary = (r"(?:is|are|was|were|be|been|being|remain[s]?|will|would|shall|should|must|"
                 r"can|could|may|might|have|has|had|do|does|did|need[s]?|required|to|not|never|no longer|\w+n't)")
    predicate_words = r'(?:(?:' + auxiliary + '|' + adverb + r')\s+){0,10}'
    passive = predicate_words.replace('{0,10}', '{0,10}?') + requirement
    continuation = r'\s+(?:and|or|but|yet|however|nevertheless|while|although)\s+'
    tail = (passive + r'(?:' + continuation + r'(?:(?:' + human_subject +
            r'|it)\s+)?' + passive + r'){0,3}')
    # Only supported parenthetical words and inter-word commas are presentation.
    # Other brackets, quotes, symbols and trailing punctuation remain unmatched.
    clause = re.sub(r'\((still|also|never|' + decision_noun + r')\)', r'\1', clause)
    clause = re.sub(r'(?<=\w),\s+(?=\w)', ' ', clause)
    clause = re.sub(r'\s+', ' ', clause).strip()
    gap = r'[\s:;]+'
    forward = human_subject + gap + r'(?P<predicate>' + tail + r')'
    reverse = r'(?P<predicate>' + passive + ')' + gap + human_subject
    elided = r'(?:it\s+)?(?P<predicate>' + tail + r')'
    for pattern in (forward, reverse, elided):
        match = re.fullmatch(pattern, clause)
        if match:
            predicates = [item[0] for item in re.finditer(passive, match['predicate'])]
            # After the first complete predicate, "yet" is the conjunction
            # admitted by tail, not an adverb modifying the next negation.
            predicates = [re.sub(r'^yet\s+', '', item) if index else item
                          for index, item in enumerate(predicates)]
            return bool(predicates) and all(_unnecessary_decision(item) for item in predicates)
    obligation = human_actor + r'\s+(?P<predicate>' + predicate_words + r')' + decision_verb
    match = re.fullmatch(obligation, clause)
    if match:
        predicate = match['predicate'].strip()
        return bool(re.search(r'\b(?:must|need[s]?|(?:have|has|had) to|required to)\b', predicate)
                    and _unnecessary_decision(predicate))
    human_decision = (r'(?:' + human_actor + r'\s+'
                      r'(?:(?:have|has|had|will|explicitly|manually)\s+)*' + decision_verb +
                      r'|' + human_subject + r')')
    negated_dependency = (r"(?:(?:you|there is)\s+)?(?:do not|don't|does not|doesn't|need not|no need to)\s+"
                          r'(?:wait|await|ask|pause|hold|halt|stop|require|need|obtain|get)\s+'
                          r'(?:(?:for|to)\s+)?' + human_decision)
    return re.fullmatch(negated_dependency, clause) is not None


def affirmative_consent(excerpt: str) -> bool:
    """Require direct consent and a complete classification of every clause.

    This finite grammar intentionally refuses unsupported harmless prose as well
    as unknown conditions. No recognized prefix, quoted example, human-decision
    vocabulary, or previous clause can exempt the remaining text from the audit.
    Provenance and policy conditions are still checked by the Controller.
    """
    if not _text(excerpt) or '\x00' in excerpt:
        return False
    text = excerpt.casefold().replace("’", "'")
    text = re.sub(r'^\s*(?:\[@taskplane\]\(plugin://[^)]+\)|@taskplane)\s*', '', text)
    if '?' in text or re.search(
        r'\b(?:if|unless|until|when|once|provided|assuming|hypothetically|maybe|perhaps|subject to|as long as)\b', text
    ):
        return False
    phase = r'(?:' + '|'.join(w.PHASES) + r')'
    check = (r'(?:required\s+)?(?:tests?|checks?)\s+'
             r'(?:(?:(?:must|should|shall|will)\s+)?(?:pass|succeed)|'
             r'(?:(?:is|are|remain|remains)\s+)?(?:required|mandatory|needed|necessary))')
    stop = r'(?:stop|pause|hold)\s+(?:at|before)\s+' + phase + r'(?:\s+phase)?'
    prefix = r'(?:now[, ]+)?(?:for this (?:task|run|release|workflow),?\s+)?(?:please\s+)?'
    actor = r'(?:(?:i (?:explicitly )?authorize (?:you|taskplane) to|run autonomously and)\s+)?'
    verb = r'(?:auto[ -]?approve|automatically approve)\s+'
    phase_list = phase + r'(?:\s*,\s*' + phase + r'){0,6}(?:\s*,?\s+and\s+' + phase + r')?'
    target = r'(?:all\s+|the\s+|each\s+)?(?:phases?|phase transitions?|' + phase_list + r')'
    direct = prefix + actor + verb + target
    request = prefix + r'(?:(?:i (?:want|need|would like)(?: you)? to|you may)\s+)?'
    workflow = (request + r'(?:start|run|execute|proceed with)\s+(?:an?\s+|the\s+|this\s+)?'
                r'(?:(?:full|end[ -]to[ -]end)\s+)?(?:auto[ -]?approved|automatically approved|autonomous)\s+'
                r'(?:full\s+)?(?:workflow|flow|run|delivery)')
    fixes_workflow = (request + r'proceed with fixes with\s+(?:an?\s+|the\s+)?'
                      r'(?:end[ -]to[ -]end\s+)?auto[ -]?approved\s+(?:workflow|flow)')
    automatic_phases = request + r'(?:run|execute)\s+(?:all\s+)?(?:release\s+)?phases\s+automatically'
    declarative = r'this is\s+(?:an?\s+)?auto[ -]?approved\s+(?:flow|workflow|run)(?:\s+all the way)?'
    # The existing document-input request has a bounded object, never a wildcard
    # capable of swallowing a human condition embedded in a technical description.
    document_input = r'(?:use 36-hour audit and retrospective document as (?:an? )?input and\s+)?'
    end_to_end = (request + document_input +
                  r'(?:start|run|execute|proceed with)\s+(?:an?\s+|the\s+)?(?:(?:full\s+)?end[ -]to[ -]end|full)\s+'
                  r'(?:flow|workflow|delivery)\s+with\s+auto[ -]?approv(?:al|e)')
    technical_goal = (r'which should resolve untracked token usage'
                      r"(?:, over 3\.5m tokens were spend outside any of the phases, it should be properly tracked where it's used "
                      r'for proper tracking and father analysis)?')
    # Supported suffixes form a complete grammar, not a prefix to be erased.
    scope = r'(?:\s+for this (?:task|run|release|workflow))?'
    through = r'(?:\s+through\s+' + phase + r')?'
    condition = r'(?:\s+after\s+' + check + r')?'
    purpose = r'(?:\s+(?:to fix (?:it )?all|' + technical_goal + r'))?'
    positive = '(?:' + '|'.join((direct, workflow, fixes_workflow, automatic_phases, end_to_end, declarative)) + ')'
    positive += scope + through + condition + purpose
    feature = (r'(?:second improvement is coming from retro as well:\s*)?'
               r'(?:remove|change|relax|update)\s+(?:the\s+)?(?:exact\s+)?'
               r'(?:word|wording)\s+(?:expectation|requirement|matching)\s+for\s+'
               r'phase approval(?:,?\s*either extend vocabulary or just accept '
               r'plain text which indicates? approval)?')
    technical = (
        feature,
        r'use taskplane to implement the settings page',
        r'we need to address and resolve all the issues',
        r'additionally run security lens and include its findings into the scope',
        r"it's ok for ask for clarification, but never ask for specific 100% match on the words to be used to proceed",
    )
    positive_seen = False

    def classified(clause: str) -> bool:
        nonlocal positive_seen
        clause = re.sub(r'\s+', ' ', clause).strip()
        if re.fullmatch(positive, clause):
            positive_seen = True
            return True
        if any(re.fullmatch(pattern, clause) for pattern in (check, stop, *technical)):
            return True
        return _unnecessary_clause(clause)

    # Decimal punctuation belongs to the bounded technical statement. All other
    # sentence punctuation is an explicit boundary. A semicolon may also belong
    # to a reversed unnecessary decision, so try the full clause before splitting.
    for sentence in re.split(r'(?<!\d)\.|\.(?!\d)|[!\n]', text):
        if not sentence.strip():
            continue
        if classified(sentence):
            continue
        clauses = sentence.split(';')
        if len(clauses) == 1 or not all(part.strip() and classified(part) for part in clauses):
            return False
    return positive_seen


def conditional_intermediate_consent(instructions: str, request: dict[str, Any]) -> bool:
    """Interpret a bounded, check-dependent offer followed by explicit assent.

    This is only used with the complete contextual question, never as standalone
    user consent. Preserve its original text; interpretation does not rewrite it.
    """
    text = re.sub(r'\s+', ' ', instructions.casefold()).strip().rstrip('?!.')
    pattern = (r'(?:approve (?:this|the) scope and )?(?:let me|may i|shall i) '
               r'(?:accept|approve) (?:the )?intermediate phases '
               r'(?:when|after|once) (?:their|the|all) required checks pass,? '
               r'(?:stopping|and stop) (?:for|before) your final (?:acceptance|approval)')
    return bool(re.fullmatch(pattern, text)
                and request.get('allowed_phases') == list(w.PHASES[:-1])
                and request.get('stop_phases') == ['retro'])


def contextual_consent(state: dict[str, Any], request: dict[str, Any], observed: datetime,
                       *, relay: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Bind an exact brief choice to the actual previously presented policy.

    This optional envelope is observed conversational evidence, just like the
    decision envelope. It cannot turn a generic approval into a policy without
    retaining the question, proposed instructions and exact policy fields.
    """
    context = request.get("choice_context")
    if context is None:
        return None
    w.require(request.get("mode") == "autonomous" and isinstance(context, dict)
              and context.get("schema") == "taskplane.policy-choice/v1",
              "decision_provenance", "Policy choice requires its complete presented context.")
    proposal = {key: request.get(key) for key in ("binding", "mode", "allowed_phases", "stop_phases", "conditions")}
    w.require(context.get("proposal") == proposal, "decision_binding",
              "Presented policy differs from this run's requested policy.")
    source = context.get("source", {})
    origin = relay["source_thread"] if relay else state["root"]
    w.require(isinstance(source, dict) and source.get("conversation") == origin
              and source.get("actor") == "assistant" and _text(source.get("reference"), 512)
              and _text(context.get("question")) and _text(context.get("instructions")),
              "decision_provenance", "Policy choice needs the actual question, instructions and presentation source.")
    from .workflow_local import timestamp
    w.require(timestamp(state["started_at"]) <= timestamp(source.get("observed_at")) < observed,
              "decision_chronology", "The automatic-policy question must follow run start and precede the response.")
    w.require(context.get("selected_label") == request.get("excerpt")
              and conversational_choice(request["excerpt"]) == "approved"
              and (affirmative_consent(context["instructions"])
                   or conditional_intermediate_consent(context["instructions"], request)), "decision_grammar",
              "Policy choice is unclear or its presented instructions do not authorize automatic phase approvals.")
    return deepcopy(dict(context))


def authorize(state: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    w.require(state.get("profile") == "native_workflow", "unsupported_authority",
              "Observed automatic authorization is available only in native_workflow.")
    w.require(not state["finished"], "approval_required", "An ended run cannot change its policy.")
    from . import delegated_approval
    w.require(request.get("schema") in {SCHEMA, delegated_approval.POLICY_SCHEMA},
              "invalid_evidence", "Approval policy schema is missing.")
    event = request.get("event_id")
    w.require(_text(event, 512), "invalid_evidence", "Policy needs an actual user event reference.")
    events = state.get("policy_events", {})
    digest = content_fingerprint(request)
    if event in events:
        w.require(events[event] == digest, "stale_checkpoint", "Conflicting policy event replay.")
        return deepcopy(state)
    w.require(content_fingerprint(request.get("binding")) == content_fingerprint(policy_binding(state)), "stale_checkpoint",
              "Policy must bind the current workspace, task, run, scope and revision.")
    relay = (delegated_approval.verify(state, request)
             if request.get("schema") == delegated_approval.POLICY_SCHEMA else None)
    w.require(relay is not None or "relay" not in request, "invalid_evidence",
              "Delegated observations require the explicit v2 policy schema.")
    source = request.get("source")
    w.require(isinstance(source, dict) and (relay is not None or (
              source.get("kind") in ("conversation", "native_prompt")
              and source.get("conversation") == state["root"])) and source.get("actor") == "user"
              and source.get("automatic") is False and _text(source.get("reference"), 512)
              and request.get("recorder") in ("root_orchestrator", "native_prompt_hook"),
              "invalid_evidence", "Policy requires observed user provenance, not an agent/tool event.")
    assert isinstance(source, dict)
    try:
        observed = datetime.fromisoformat(str(source.get("observed_at", "")).replace("Z", "+00:00"))
        started = datetime.fromisoformat(state["started_at"].replace("Z", "+00:00"))
        w.require(observed.tzinfo is not None and started.tzinfo is not None
                  and observed <= datetime.now(observed.tzinfo), "invalid_evidence", "Invalid authorization time.")
        # Instructions can precede start, but cannot come from an older run.
        w.require(request.get("request_reference") == state.get("request_provenance", {}).get("reference")
                  or observed >= started, "invalid_evidence", "Pre-start authorization must reference this run's request.")
    except (ValueError, TypeError):
        raise w.Refusal("invalid_evidence", "Policy needs a valid observed timestamp.") from None
    excerpt = request.get("excerpt")
    w.require(_text(excerpt), "invalid_evidence", "Preserve the actual additional instructions.")
    assert isinstance(excerpt, str)
    mode = request.get("mode")
    w.require(mode in ("manual", "autonomous"), "invalid_evidence", "Choose manual or autonomous approval.")
    normalized = excerpt.casefold()
    choice_context = contextual_consent(state, request, observed, relay=relay)
    if mode == "autonomous":
        w.require(affirmative_consent(excerpt) or choice_context is not None, "approval_required",
                  "Automatic approval intent is unclear. Ask whether the user wants automatic phase approvals for this run, and preserve their answer in their own words. Negative, quoted or feature-only wording stays manual.")
    else:
        w.require(re.search(r"manual|(?:stop|disable|revoke|cancel).*(?:auto|automatic)", normalized),
                  "approval_required", "Preserve an explicit instruction to stop or return to manual approval.")
    allowed, stops = request.get("allowed_phases", []), request.get("stop_phases", [])
    w.require(isinstance(allowed, list) and isinstance(stops, list)
              and all(isinstance(p, str) and p in w.PHASES for p in allowed + stops)
              and len(set(allowed)) == len(allowed) and len(set(stops)) == len(stops)
              and (bool(allowed) if mode == "autonomous" else not allowed),
              "invalid_evidence", "Declare exact allowed phases and mandatory stop phases.")
    conditions = request.get("conditions", [])
    w.require(isinstance(conditions, list) and len(conditions) <= 32, "invalid_evidence", "Invalid policy conditions.")
    ids = {"user_instructions"}
    for condition in conditions:
        w.require(isinstance(condition, dict) and _text(condition.get("id"), 80)
                  and condition["id"] not in ids and condition.get("kind") in ("observed", "required_check")
                  and _text(condition.get("instruction"))
                  and (condition["kind"] != "required_check" or _text(condition.get("check"), 200)),
                  "invalid_evidence", "Conditions need unique IDs, instructions and a supported kind.")
        ids.add(condition["id"])
    s = deepcopy(state)
    history = s.setdefault("policy_history", [])
    w.require(len(history) < 256, "state_unavailable", "Policy history limit reached; do not reset active state.")
    policy = {"schema": SCHEMA, "id": event, "revision": len(history) + 1, "mode": mode,
              "binding": policy_binding(state), "authorized_scope": deepcopy(state["scope"]),
              "allowed_phases": allowed, "stop_phases": stops,
              "conditions": [{"id": "user_instructions", "kind": "observed",
                              "instruction": choice_context["instructions"] if choice_context else excerpt}] + conditions,
              "provenance": {"source": deepcopy(source), "recorder": request["recorder"], "excerpt": excerpt},
              "assurance": "observed", "request_digest": digest}
    if choice_context is not None:
        policy["provenance"]["choice_context"] = choice_context
    if relay is not None:
        policy["provenance"].update(schema=delegated_approval.POLICY_SCHEMA, relay=relay)
        policy["source_assurance"] = "relayed_observation"
        policy["host_attested"] = False
    policy["digest"] = content_fingerprint(policy)
    history.append(policy)
    s["approval_policy"] = deepcopy(policy)
    s["policy_suspension"] = None
    s.setdefault("policy_events", {})[event] = digest
    s["revision"] += 1
    return s


def suspend(state: dict[str, Any], reason: str) -> None:
    if state.get("approval_policy", {}).get("mode") == "autonomous":
        state["policy_suspension"] = reason


def validate_history(state: dict[str, Any]) -> None:
    history = state.get("policy_history", [])
    w.require(isinstance(history, list) and len(history) <= 256, "state_unavailable", "Invalid policy history.")
    for index, policy in enumerate(history):
        w.require(isinstance(policy, dict) and policy.get("schema") == SCHEMA
                  and policy.get("revision") == index + 1 and policy.get("assurance") == "observed"
                  and policy.get("digest") == content_fingerprint({k:v for k,v in policy.items() if k != "digest"})
                  and all(policy.get("binding", {}).get(k) == state[k] for k in ("workspace", "root", "run")),
                  "state_unavailable", "Corrupt or foreign policy history.")
    if history:
        w.require(state.get("approval_policy") == history[-1], "state_unavailable", "Policy version mismatch.")
    else:
        w.require(not state.get("approval_policy"), "state_unavailable", "Policy has no history.")


def decision_authorized(state: dict[str, Any], decision: dict[str, Any]) -> bool:
    if decision.get("human") is True and decision.get("automatic") is False:
        return bool(decision.get("kind", "human") == "human")
    if state.get("profile") != "native_workflow" or decision.get("kind") != "policy":
        return False
    policy = next((p for p in state.get("policy_history", []) if p["digest"] == decision.get("policy_digest")), None)
    assessment = decision.get("assessment", {})
    stage = next((v for v in state["visits"] if v["id"] == decision.get("binding", {}).get("visit")), None)
    return bool(policy and stage and policy["mode"] == "autonomous"
                and stage["phase"] in policy["allowed_phases"] and stage["phase"] not in policy["stop_phases"]
                and decision.get("human") is False and decision.get("automatic") is True
                and decision.get("choice") == "approved" and assessment.get("binding") == decision.get("binding")
                and assessment.get("policy_digest") == policy["digest"]
                and {c["id"] for c in assessment.get("conditions", [])} == {c["id"] for c in policy["conditions"]}
                and all(c.get("status") == "pass" for c in assessment.get("conditions", [])))


def automatic_decision(state: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    w.require(state.get("profile") == "native_workflow", "unsupported_authority", "Automatic approval is unavailable in protected_host.")
    w.require(assessment.get("schema") == ASSESSMENT and isinstance(assessment.get("binding"), dict),
              "invalid_evidence", "Supply a checkpoint-bound policy assessment.")
    event = "policy:" + str(assessment.get("policy_digest")) + ":" + str(assessment["binding"].get("checkpoint"))
    if event in state["decisions"]:
        old = state["decisions"][event]
        w.require(old.get("assessment") == assessment, "stale_checkpoint", "Conflicting automatic decision replay.")
        return deepcopy(old)
    policy = state.get("approval_policy") or {}
    w.require(policy.get("mode") == "autonomous" and not state.get("policy_suspension"),
              "approval_required", state.get("policy_suspension") or "Manual approval is active; explicit authorization is required.")
    stage = w.current(state)
    w.require(stage["decision"] == "awaiting_human_approval" and stage["packet"], "approval_required", "Submit evidence before automatic approval.")
    w.require(stage["phase"] in policy["allowed_phases"] and stage["phase"] not in policy["stop_phases"],
              "approval_required", "This phase requires a human checkpoint under the current policy.")
    packet = stage["packet"]
    w.require(content_fingerprint(assessment["binding"]) == content_fingerprint(w.binding(state, packet)) and assessment.get("policy_digest") == policy["digest"],
              "stale_checkpoint", "Assessment belongs to an old policy or checkpoint.")
    outer = policy["authorized_scope"]
    w.require(state["scope"]["criteria"] == outer["criteria"]
              and state["scope"].get("verification_inputs", []) == outer.get("verification_inputs", [])
              and all(set(paths) <= set(outer["paths"][phase]) for phase,paths in state["scope"]["paths"].items())
              and not packet.get("route_change"), "approval_required", "Scope or route changes require renewed human authorization.")
    output = packet["output"]
    from .workflow_evidence import effective_checks
    checks = effective_checks(state, output)
    if stage["phase"] == "build":
        w.require(checks and all(c.get("status") == "pass" for c in checks if c.get("required", True))
                  and not output.get("known_gaps"),
                  "approval_required", "Build checks or unresolved gaps require review.")
    if stage["phase"] == "evaluate":
        w.require(all(c.get("status") == "pass" for c in output["criterion_results"].values())
                  and not output.get("unknowns_and_failures"), "approval_required", "Evaluation is failed or unknown.")
    if stage["phase"] == "engineering":
        w.require(not any(f.get("blocking") or str(f.get("severity", "")).casefold() in
                          ("p0", "p1", "critical", "high", "blocker") for f in output.get("findings", [])),
                  "approval_required", "Unresolved Engineering blockers require review.")
    items = assessment.get("conditions")
    w.require(isinstance(items, list) and len(items) == len(policy["conditions"])
              and all(isinstance(c, dict) for c in items)
              and {c.get("id") for c in items} == {c["id"] for c in policy["conditions"]},
              "invalid_evidence", "Assess every user condition, including the original instructions.")
    assert isinstance(items, list)
    for c in items:
        w.require(c.get("status") == "pass", "approval_required", "A required condition is failed or unknown: " + str(c.get("id")))
        refs = c.get("evidence")
        w.require(_text(c.get("explanation")) and isinstance(refs, list) and refs
                  and all(isinstance(p, str) and p in packet["manifest"] for p in refs),
                  "invalid_evidence", "Condition assessment needs an explanation and sealed evidence files.")
        rule = next(rule for rule in policy["conditions"] if rule["id"] == c["id"])
        if rule["kind"] == "required_check":
            w.require(any(check.get("name") == rule["check"] and check.get("status") == "pass" for check in checks),
                      "approval_required", "Named required check is absent or not passing.")
    return {"event_id": event, "kind": "policy", "human": False, "automatic": True,
            "choice": "approved", "binding": w.binding(state, packet), "policy_digest": policy["digest"],
            "policy_id": policy["id"], "policy_revision": policy["revision"], "assurance": "observed",
            "provenance": deepcopy(policy["provenance"]), "assessment": deepcopy(assessment)}
