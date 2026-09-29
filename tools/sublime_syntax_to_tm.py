#!/usr/bin/env python3
"""Convert a .sublime-syntax (YAML) into a VSCode .tmLanguage.json grammar.

Sublime syntaxes are a context/push/pop STATE MACHINE. TextMate grammars are a
begin/end/repository tree. The earlier version of this script translated the
`match:` rules but silently dropped every `push:`/`set:`/`pop:`, which in
AtariBASIC.sublime-syntax is 166 transitions -- the whole machine. The output
still "worked" (the line-number rule matched) but nothing ever entered
`code_line`, so keywords, variables and strings were never scoped.

The translation now models the state machine:

    match: M            ->  { begin: M,
    scope: S                  beginCaptures: { 0: {name: S} },
    push:  C                  end: <C's termination>,
                              patterns: [ {include: '#C'} ] }

Two details that matter and are easy to get wrong:

* The rule's `scope` must become **beginCaptures**, NOT the region's `name`.
  A region `name` applies to begin + content + end, which would smear (say)
  `keyword.print` across the whole rest of the line.

* Termination. 41 of this grammar's 59 contexts have no explicit `pop` -- they
  rely on Sublime's `prototype`, which pops at `$`. Contexts that do pop mostly
  consume a delimiter (`:`, `)`, `"`). So the end condition is the alternation
  of a context's real pop patterns, falling back to `(?=$)` -- end of line,
  consuming nothing. A pop matching the empty string is Sublime's
  "pop if nothing else matched" idiom and maps to the same fallback.

`set:` is pop-then-push. We model it as a push: since essentially everything
here terminates at end of line, the visible tokenization is the same, and the
alternative (splicing sibling regions) cannot be expressed in TextMate at all.

Handled Sublime-isms:
  - `variables:` interpolation {{name}} (recursive)
  - the bare `=` key, which PyYAML reads as the yaml `value` tag
  - `captures:` / `push:` / `set:` / `pop:` / `include:`
  - `meta_scope:` -> name, `meta_content_scope:` -> contentName
  - Oniguruma `\\h` -> `[0-9a-fA-F]`; bare `(?i)` -> scoped `(?i:...)`

Usage:
    python3 sublime_syntax_to_tm.py IN.sublime-syntax OUT.tmLanguage.json \\
        --scope source.ataribasic --name "Atari BASIC"
"""
import argparse
import json
import re
import sys

import yaml


class SublimeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Sublime's bare `=` key."""


def _construct_value(loader, node):
    """Sublime uses a bare `=` key, which PyYAML maps to the yaml `value` tag."""
    return loader.construct_scalar(node)  # type: ignore[arg-type]


SublimeLoader.add_constructor('tag:yaml.org,2002:value', _construct_value)

def _construct_str(loader, node):
    """Force a scalar to stay a string (see the bool note below)."""
    return loader.construct_scalar(node)  # type: ignore[arg-type]


# YAML 1.1 reads bare ON/OFF/YES/NO/TRUE/FALSE as booleans. Atari BASIC has a
# statement literally named ON, so `- match: ON` arrived as the boolean True and
# silently produced `"begin": true` -- a pattern that matches everything,
# swallowing PRINT and scoping it as a variable. Keep these as plain strings;
# a regex is always text here.
SublimeLoader.add_constructor('tag:yaml.org,2002:bool', _construct_str)


def expand_vars(text, variables, depth=0):
    """Recursively expand {{var}} references."""
    if not isinstance(text, str) or depth > 12:
        return text

    def sub(m):
        return str(variables.get(m.group(1), m.group(0)))

    out = re.sub(r'\{\{(\w+)\}\}', sub, text)
    return expand_vars(out, variables, depth + 1) if '{{' in out and out != text else out


def fix_regex(rx):
    """Translate Oniguruma constructs VSCode's regex engine lacks."""
    if not isinstance(rx, str):
        return rx
    rx = rx.replace('\\h', '[0-9a-fA-F]')

    # A negated class like `[^"]*` is bounded in Sublime by the context it lives
    # in: contexts are per-line and pop at ':' or EOL, so `[^"]*(")` can only
    # ever reach a quote in the CURRENT statement. TextMate has no such bound --
    # the same pattern scans forward across the statement delimiter and pairs
    # with a quote in a LATER statement. In `PRINT "A":PRINT "B"` that made the
    # closing-quote rule swallow `:PRINT "`, so the second PRINT never got its
    # keyword scope.
    #
    # Excluding ':' from these classes restores Sublime's implicit boundary.
    # Only classes that already exclude the quote are touched, so character
    # classes meant to match a literal ':' are left alone.
    rx = re.sub(r'\[\^([^\]]*")([^\]]*)\]',
                lambda m: '[^%s%s:]' % (m.group(1), m.group(2))
                if ':' not in m.group(0) else m.group(0),
                rx)

    # A BARE inline flag group `(?i)` applies to the REST of the pattern in
    # Oniguruma; VSCode rejects it as an invalid group. Rewrite to the scoped
    # form `(?i:...)`. Sublime also nests them inside a group that already
    # applies the same flag, where wrapping would unbalance the parens -- so a
    # redundant inner flag is dropped. Process left to right, tracking flags.
    active = set()
    while True:
        m = re.search(r'\(\?([a-zA-Z]+)\)', rx)
        if not m:
            break
        flags, start, end = m.group(1), m.start(), m.end()
        for em in re.finditer(r'\(\?([a-zA-Z]+):', rx[:start]):
            active.update(em.group(1))
        if set(flags) <= active:
            rx = rx[:start] + rx[end:]              # redundant -- drop it
        else:
            rx = rx[:start] + '(?' + flags + ':' + rx[end:] + ')'
            active.update(flags)
    return rx


def conv_captures(caps, variables):
    out = {}
    if isinstance(caps, dict):
        for k, v in caps.items():
            if isinstance(v, str):
                out[str(k)] = {'name': expand_vars(v, variables)}
            elif isinstance(v, dict) and 'name' in v:
                out[str(k)] = {'name': expand_vars(v['name'], variables)}
    return out


def targets_of(rule):
    """The context(s) a rule transitions into, innermost last.

    A target is normally a context NAME, but Sublime also allows an ANONYMOUS
    context written inline as a list of rules (the 6502 grammar uses this for
    quoted strings and `{...}` groups). Those arrive as dicts inside the list
    and are handled by the caller, which materialises them into the repository.
    """
    t = rule.get('push', rule.get('set'))
    if t is None:
        return []
    if isinstance(t, str):
        return [t]
    if isinstance(t, list):
        # A bare list of rule dicts IS one anonymous context, not a list of
        # targets. A list of strings is a push of several named contexts.
        if t and all(isinstance(x, dict) for x in t):
            return [t]
        return list(t)
    return [t]


# Sublime applies `prototype` (here: `match: \s+`) BEFORE the rules of a context
# compete, so leading whitespace is always consumed first and every rule matches
# at the first non-blank character. TextMate has no prototype: all patterns in a
# context race by leftmost position, so a rule beginning with `\s*` can start at
# the whitespace and beat a keyword rule that can only start one column later.
#
# That is exactly how `10 PRINT "HI"` broke: the implicit-LET rule
# `(LE[T.])?\s*([A-Z][A-Z0-9]*)` matched " PRINT" from the space (index 2) while
# the PRINT rule could only match at index 3, so PRINT scoped as a variable.
# Note `10PRINT` (no space) tokenized correctly -- the tell-tale asymmetry.
#
# Anchoring such patterns with \G-like semantics is not available either, so we
# strip a LEADING optional-whitespace element from the pattern. Whitespace is
# still skipped by the context's own `\s+` rule; the keyword rules then win on
# equal footing, preserving Sublime's rule-order precedence.


def strip_leading_optional_ws(rx):
    """Stop a pattern from STARTING on whitespace.

    A bare leading `\\s*` is removed. When an optional group precedes it,
    `(LE[T.])?\\s*(...)` -> `(?:(LE[T.])\\s*)?(...)`: the blank moves INSIDE
    the optional group, so the pattern still cannot begin on a blank but the
    group and the text after it may still be separated by one. Dropping the
    `\\s*` outright (the earlier fix) broke `LET A`, which then scoped LET as
    a variable name.
    """
    if not isinstance(rx, str):
        return rx, False
    m = re.match(r'^((?:\([^()]*\)\?|\[[^\]]*\]\?)?)\\s\*', rx)
    if not m:
        return rx, False
    opt = m.group(1)
    if opt:
        return '(?:%s\\s*)?' % opt[:-1] + rx[m.end():], True
    return rx[m.end():], True


class Converter:
    def __init__(self, contexts, variables):
        self.contexts = contexts
        self.variables = variables
        self._end_cache = {}
        self._literal_cache = {}
        self._anon = {}
        self._seq = {}
        self.ws_stripped = 0
        self.empty_begins = 0

    def intern_anon(self, rules):
        """Give an anonymous inline context a name in `self.contexts`.

        Sublime allows `push:`/`set:` to carry a literal list of rules instead
        of a context name. Keyed by identity so the same inline block is
        interned once.
        """
        key = id(rules)
        if key in self._anon:
            return self._anon[key]
        name = f'anon_{len(self._anon)}'
        self._anon[key] = name
        self.contexts[name] = rules
        return name

    def ends_at_delimiter(self, ctx_name):
        """Whether a region entered into `ctx_name` should yield at ':'.

        Almost every context is part of a statement and must hand the delimiter
        back so the next statement gets scoped. A context that consumes literal
        text must NOT: a string may legitimately contain a colon, and
        `X.18,#1,0,0,"S:"` has one. Those contexts are recognised by carrying a
        `meta_scope` of string/comment, matching how Sublime treats them as
        atomic spans rather than statement structure.
        """
        if ctx_name in self._literal_cache:
            return self._literal_cache[ctx_name]

        # `code_line` is the context that OWNS the statement delimiter: it has
        # its own `match: ':'` rule scoping it support.token.delimiter.statement
        # before starting the next statement. If its region yielded at ':' like
        # everything else, it would close one character early and that rule
        # could never run -- the colon came out with no scope at all, even on a
        # line as simple as `30 :`, and the delimiter was invisible to themes.
        # Everything nested INSIDE it still yields, which is what hands the
        # colon back up to here.
        if ctx_name == 'code_line':
            self._literal_cache[ctx_name] = False
            return False

        # A context that OWNS the delimiter -- it has its own non-pop rule
        # for ':' (line_and_statement_should_end, expr_start_last and
        # dead_code all turn the rest of the line into dead code there), or
        # it runs a statement list (marked_dead includes code_line) -- must
        # not hand ':' back, or that rule never runs and `BYE:?"X"` scoped
        # the PRINT as live code.
        if self.owns_delimiter(ctx_name):
            self._literal_cache[ctx_name] = False
            return False

        literal = False
        rules = [r for r in self.contexts.get(ctx_name) or [] if isinstance(r, dict)]
        for r in rules:
            if not isinstance(r, dict):
                continue
            meta = r.get('meta_scope') or r.get('meta_content_scope')
            if isinstance(meta, str) and (meta.startswith('string')
                                          or meta.startswith('comment')):
                literal = True
                break
        self._literal_cache[ctx_name] = not literal
        return not literal

    def owns_delimiter(self, ctx_name, _seen=None):
        """Has its own non-pop ':' rule, or includes a statement list."""
        seen = set() if _seen is None else _seen
        if ctx_name in seen:
            return False
        seen.add(ctx_name)
        for r in self.contexts.get(ctx_name) or []:
            if not isinstance(r, dict):
                continue
            inc = r.get('include')
            if isinstance(inc, str):
                if inc in STATEMENT_LIST_CONTEXTS or self.owns_delimiter(inc, seen):
                    return True
                continue
            if r.get('match') in (':', "':'") and not r.get('pop'):
                return True
        return False

    def end_condition(self, ctx_name):
        """The regex that terminates a region entered into `ctx_name`.

        Pop rules are reached two ways: declared directly in the context, or
        inherited through `include:`. 16 of this grammar's contexts pop ONLY via
        an include -- `cmd_print` etc. include `mixin_statement_end`, whose
        `match: :|$ / pop: true` is what ends a statement. Missing those made
        every statement region run to end of line, so `GR. 8:COLOR 1` scoped
        only the first statement and swallowed the rest.

        Falls back to `(?=$)` (end of line, consuming nothing), which is what
        Sublime's prototype pop does for the contexts with no pop of their own.
        """
        if ctx_name in self._end_cache:
            return self._end_cache[ctx_name]

        self._end_cache[ctx_name] = ('(?=$)', None)   # guard against cycles
        yields = self.ends_at_delimiter(ctx_name)

        alts, scope = [], None
        empty_pop = False
        seen = set()
        stack = [ctx_name]
        while stack:
            name = stack.pop()
            if name in seen:
                continue
            seen.add(name)
            for r in self.contexts.get(name) or []:
                if not isinstance(r, dict):
                    continue
                inc = r.get('include')
                if isinstance(inc, str) and not inc.startswith('scope:'):
                    stack.append(inc)
                    continue
                # `set:` REPLACES the current context, so whatever ends the
                # replacement also ends this region. An unconditional set
                # (`match: ''`) is how this grammar chains one_expr ->
                # real_expr -> expr_eat_modifier ... down to the context that
                # actually includes mixin_statement_end. Without following it,
                # the ':' statement delimiter is never found.
                if r.get('set') is not None and r.get('match') == '':
                    t = r['set']
                    stack.extend(t if isinstance(t, list) else [t])
                    continue
                if not r.get('pop'):
                    continue
                m = r.get('match')
                if not isinstance(m, str):
                    continue
                m = fix_regex(expand_vars(m, self.variables))
                # `$` is the prototype's own EOL pop, already the fallback.
                if m == '$':
                    continue
                # `match: '.' / pop: true` is a CATCH-ALL, not a delimiter: it
                # means "whatever comes next, scope it here and then leave".
                # Used as a TextMate `end` it is disastrous, because end is
                # tested BEFORE the body patterns -- the region closes on its
                # own first character, so the context's scope never lands and
                # that character is consumed unscoped. That is why `IF` never
                # produced ctx.cmd_if and why the `N` of `NOT` vanished.
                #
                # The context's real terminator is the statement delimiter, so
                # treat this exactly like the empty pop: end at ':'/EOL and let
                # the body patterns do the scoping.
                if m == '.':
                    empty_pop = True
                    continue
                # `match: '' / pop: true` is Sublime's "pop if nothing else
                # matched" idiom: the context ends as soon as its own rules stop
                # applying. TextMate has no such trigger, so the region must end
                # wherever the ENCLOSING statement would -- i.e. at ':' or EOL.
                # Treating this as "no delimiter" left expr_wants_operator open
                # forever, swallowing every statement after the first ':'.
                if m == '':
                    empty_pop = True
                    continue
                alts.append(m)
                if scope is None and r.get('scope'):
                    scope = expand_vars(r['scope'], self.variables)

        if alts:
            # Always allow end-of-line to close the region too: a BASIC line is
            # self-contained, and without this a region whose delimiter is
            # missing would bleed into the following line.
            #
            # `:` is looked at but NOT consumed. Sublime pops on it and the
            # popped-to context scopes it, but a TextMate region that eats the
            # ':' leaves the enclosing code_line resuming AFTER it, which then
            # cannot start the next statement. Matching it with a lookahead
            # hands it back.
            parts = []
            for a in dict.fromkeys(alts):
                if a in (':|$', ':'):
                    parts.append('(?=:|$)')
                    scope = None       # the delimiter is scoped by code_line
                else:
                    parts.append(f'(?:{a})')
            # A context with BOTH a real pop and the empty "pop when nothing
            # matches" fallback (expr_wants_operator pops on GOTO/GOSUB and also
            # on '') must still yield at the statement delimiter -- otherwise it
            # stays open across ':' and swallows every later statement.
            if empty_pop and yields and not any('(?=:|$)' == p for p in parts):
                parts.append('(?=:)')
            # ...and it must ALSO pop wherever none of its own rules applies,
            # which is what `match: ''` means. Without this, `?A;B` left
            # expr_wants_operator open over the ';', swallowing PRINT's
            # separator unscoped. `(?=\S)` lets the whitespace skip run first.
            if empty_pop:
                starts = []
                for n in sorted(seen):   # every context the set-chain crosses
                    starts += self.start_patterns(n)
                if starts:
                    parts.append('(?=\\S)(?!' + '|'.join(
                        '(?:%s)' % s for s in dict.fromkeys(starts)) + ')')
            rx = '(?:' + '|'.join(parts) + '|(?=$))'
            result = (rx, scope)
        elif empty_pop:
            # `match: '' / pop: true` -- pop as soon as the context's own rules
            # stop applying. var_eat_subscript is the archetype: after a
            # variable name it accepts an optional '(' subscript and otherwise
            # pops AT ONCE. Ending such a region at ':'/EOL instead made it span
            # the rest of the line, so `FOR I=1 TO 10` swallowed `TO` inside the
            # subscript region and never scoped it.
            #
            # The faithful translation is a zero-width end that fires as soon as
            # none of the context's own begin patterns match here.
            starts = []
            for r in self.contexts.get(ctx_name) or []:
                if not isinstance(r, dict):
                    continue
                m = r.get('match')
                if isinstance(m, str) and m not in ('', '$') and not r.get('pop'):
                    starts.append(fix_regex(expand_vars(m, self.variables)))
            if starts:
                rx = '(?!' + '|'.join(f'(?:{s})' for s in dict.fromkeys(starts)) + ')'
                result = (rx, None)
            else:
                result = ('(?=:|$)' if yields else '(?=$)', None)
        else:
            # No pop at all: Sublime relies on the prototype's end-of-line pop.
            # Still yield at ':' -- a statement region that runs past the
            # delimiter prevents the next statement from ever being scoped.
            result = ('(?=:|$)' if yields else '(?=$)', None)

        self._end_cache[ctx_name] = result
        return result

    def conv_rule(self, rule):
        """Convert one Sublime rule into a TextMate pattern."""
        if not isinstance(rule, dict):
            return None

        if 'include' in rule:
            target = rule['include']
            if not isinstance(target, str) or target.startswith('scope:'):
                return None
            return {'include': '#' + target}

        if 'match' not in rule:
            return None                       # meta_* handled at context level
        if rule.get('pop'):
            return None                       # becomes the enclosing region's end

        match_rx = fix_regex(expand_vars(rule['match'], self.variables))
        # "The statement ended too early" (`\s*([,;:]|$)` -> syntax_error in
        # one_expr/two_exprs) must KEEP its leading blanks: it has to start
        # on the space so it beats the generic whitespace skip and the
        # region's `(?=:)` end, both of which would otherwise hand the ':'
        # back as a normal separator (`LET A =   :` was not flagged).
        delim_err = targets_of(rule) == ['syntax_error'] and \
            (':' in rule['match'] or '$' in rule['match'])
        stripped = False
        if not delim_err:
            match_rx, stripped = strip_leading_optional_ws(match_rx)
        if stripped:
            self.ws_stripped += 1
        scope = expand_vars(rule['scope'], self.variables) if rule.get('scope') else None
        captures = conv_captures(rule['captures'], self.variables) \
            if rule.get('captures') else None

        dests = targets_of(rule)
        if not dests:
            pat = {'match': match_rx}
            if scope:
                pat['name'] = scope
            if captures:
                pat['captures'] = captures
            return pat

        # "Statement ended too early": emit as a plain match that consumes
        # the blank, the delimiter and the rest of the line with the error
        # scope -- what Sublime's syntax_error (pop only at $) paints. A
        # region would yield ':' back through its (?=:) end.
        if delim_err:
            err = 'invalid.error.syntax.ataribasic'
            for r in self.contexts.get('syntax_error') or []:
                if isinstance(r, dict) and r.get('meta_scope'):
                    err = expand_vars(r['meta_scope'], self.variables)
            return {'match': '(?:%s).*' % match_rx, 'name': err,
                    '_delim_err': True}

        # `match: '' / set: syntax_error` is Sublime's last-resort fallback: it
        # fires only when no earlier rule in the context matched. A TextMate
        # region with `begin: ""` has no such ordering privilege -- it matches at
        # EVERY position, and sitting before the rules that follow it, it wins
        # and runs to end of line. That is what kept `GR. 8:COLOR 1` from ever
        # scoping the second statement: the catch-all swallowed the rest.
        #
        # Anchor it to a position where the context genuinely cannot continue:
        # a non-blank that is not the statement delimiter.
        if match_rx == '':
            match_rx = '(?=[^\\s:])'
            self.empty_begins += 1
            empty_fallback = True
        else:
            empty_fallback = False

        # A transition becomes a begin/end region around the destination.
        # An anonymous inline context is materialised into the repository under
        # a generated name so it can be referenced like any other.
        dests = [self.intern_anon(d) if isinstance(d, list) else d
                 for d in dests]

        # Bracket context: the destination LOOPS until a closer that SETS a
        # continuation (expr_till_close: `\) -> set: expr_wants_operator`).
        # Nested inside, that closer can never end the bracket region, so an
        # extra ')' was accepted and an unclosed '(' ended quietly at ':'.
        # Push on the opener, pop ON the closer, continue after it:
        #   A (?=opener) .. [B opener..closer] then continuation rules
        if len(dests) == 1:
            closer = self.bracket_closer(dests[0])
            if closer:
                return self.bracket_region(match_rx, scope, captures,
                                           dests[0], closer)
        end_rx, end_scope = self.end_condition(dests[-1])

        # An error/fallback span must still yield at ':' so the next statement
        # can be scoped -- inheriting only the destination's `(?=$)` would run
        # it to end of line and swallow the rest of the statements.
        if empty_fallback and ':' not in end_rx \
                and self.ends_at_delimiter(dests[-1]):
            end_rx = '(?:(?=:)|' + end_rx + ')'

        # A ONE-SHOT destination (every rule transitions: set/pop) makes a
        # single decision and is then gone in Sublime -- its `set:` child
        # REPLACES it. TextMate only nests, so without help the chain of
        # replaced states stays open until ':'/EOL and hides the statement
        # underneath (PRINT's ';' after an expression was unreachable).
        # `(?!\G)` closes the region as soon as scanning has moved past its
        # own start, i.e. right after the child it chose has ended, so the
        # chain collapses back to the nearest LOOP context. `(?<=\S)` keeps a
        # whitespace skip from counting as "moved past".
        # For a stacked `set: [A, B]` the region must survive B (innermost,
        # e.g. required_args) and let A (expr_wants_operator) take its turn,
        # so closing "once past start" would drop A. Only close early when
        # there is a single destination.
        real = [d for d in dests if isinstance(d, list) or d in self.contexts]
        if len(real) == 1 and self.is_oneshot(
                real[0] if not isinstance(real[0], list)
                else self.intern_anon(real[0])):
            end_rx = '(?!\\G)(?<=\\S)|' + end_rx
        elif len(real) > 1 and all(isinstance(d, str) and self.is_oneshot(d)
                                   for d in real[:-1]):
            # Stacked set of one-shots (`[expr_wants_operator, required_args]`):
            # close once past start UNLESS an outer context's token is next
            # (the operator after `PEEK(1)`), so `;` after it is not swallowed.
            outer = []
            for d in real[:-1]:
                # Tokens the outer context CONSUMES and stays for. Its pop
                # rules (`GOTO|GOSUB -> pop`) belong to the enclosing
                # statement, so they must close us, not keep us open.
                outer += [p for p in self.start_patterns(d, pops=False)]
            if outer:
                end_rx = '(?!\\G)(?<=\\S)(?!%s)|%s' % (
                    '|'.join('(?:%s)' % p for p in outer), end_rx)

        region = {'begin': match_rx, 'end': end_rx}
        # `(?<=\S)` stops a leading whitespace skip counting as "moved past
        # start". A region that begins zero-width on a non-blank has no
        # leading whitespace, and there the guard kept it open after a
        # trailing blank (`POKE 1 ,2` flagged the comma). Drop it.
        if match_rx.startswith('(?=[^\\s'):
            region['end'] = end_rx.replace('(?!\\G)(?<=\\S)', '(?!\\G)', 1)
        # Sublime tries a context's rules IN ORDER, and its pop is just one
        # of them; TextMate tries a region's `end` FIRST. With the end being
        # `(?=:|$)`, a body rule for the delimiter (`one_expr`'s "statement
        # ended before any value" -> syntax_error) could never win. Trying
        # the end last restores Sublime's order -- but ONLY where the end
        # merely yields at the delimiter. A real terminator (a string's
        # closing quote) must stay first, or body rules swallow it.
        core = end_rx.replace('(?!\\G)(?<=\\S)|', '', 1)
        if False:  # applyEndPatternLast: tried and rejected, see below
            # Sublime tries a context's rules in order; TextMate tries `end`
            # first. applyEndPatternLast looked like the fix for delimiter
            # errors, but the one-shot `(?!\G)(?<=\S)` close MUST be tried
            # first -- delayed, body rules re-claimed the ':' after every
            # finished expression (`A=1:B=2` went red). The real fix is in
            # conv_rule: keep the error rule's own leading \s*.
            region['applyEndPatternLast'] = 1

        # CRITICAL: the rule's scope belongs to the matched text only. Using it
        # as the region `name` would apply it to the entire region.
        begin_caps = dict(captures) if captures else {}
        if scope:
            begin_caps.setdefault('0', {'name': scope})
        if begin_caps:
            region['beginCaptures'] = begin_caps
        if end_scope:
            region['endCaptures'] = {'0': {'name': end_scope}}

        # Sublime pushes a LIST of contexts as a stack: the LAST entry is
        # entered first and sits innermost, and the ones before it apply only
        # AFTER it pops (`set: [expr_wants_operator, required_args]` means
        # "parse the argument list, THEN expect an operator"). That is a
        # SEQUENCE, not a set of alternatives.
        #
        # Emitting them as sibling includes gets it wrong in both directions:
        # in source order the outer context matched '(' first, and in either
        # order the inner context's own catch-all stays live as a sibling
        # forever, so after `PEEK(A)` closed its parens required_args caught the
        # following '+' and flagged `+256*PEEK(A+N1)` invalid.
        #
        # Build the sequence properly instead: the innermost context, with the
        # outer ones nested INSIDE its regions so they become reachable only
        # once it has finished.
        dests = [d for d in dests if d in self.contexts]
        if len(dests) > 1:
            inner, outer = dests[-1], list(reversed(dests[:-1]))
            region['patterns'] = [{'include': '#' + self.intern_sequence(inner, outer)}]
        else:
            region['patterns'] = [{'include': '#' + d} for d in dests]
        if not region['patterns']:
            del region['patterns']

        # A destination whose only content is `meta_content_scope` (cmd_rem,
        # comment) contributes nothing through `include` -- TextMate ignores
        # contentName on an included repository entry. Lift it onto the region.
        inner_scope = self.content_scope_of(dests[-1])
        if inner_scope:
            region['contentName'] = inner_scope

        # Same for `meta_scope`, which covers the delimiters too (Sublime
        # applies it to the whole span including the text that pushed and
        # popped). TextMate ignores `name` on an included repository entry just
        # as it ignores contentName, so without lifting it here a string body
        # got no string.quoted scope at all -- quoted text was invisible to
        # every theme. Only set it when the rule did not already name the
        # matched text, so an explicit scope on the pushing rule still wins.
        meta_scope = self.meta_scope_of(dests[-1])
        if meta_scope and 'name' not in region:
            region['name'] = meta_scope
            # ...but only while that context actually lasts. The destination
            # leaves on its own closing delimiter, and because that delimiter is
            # matched by a rule INSIDE the destination, the outer region's `end`
            # never sees it -- the scope ran to end of line, so
            # `?#1;"(";M;") ";:RET.` left the whole tail marked string.quoted.
            # Bound the region to that exit.
            #
            # The exit may be a `pop: true` or a `set:` to another context
            # (expr_eat_string hands off to expr_wants_operator after the
            # closing quote); both end this scope.
            exit_rx = self.exit_pattern(dests[-1])
            if exit_rx and region.get('end') == '(?=$)':
                # Consume the delimiter here rather than looking ahead at it:
                # the destination's body rule would otherwise match it first
                # (a lookahead is zero-width, so the body keeps its turn) and
                # every quote came out scoped as an opener with the region never
                # closing. Whatever scope the destination put on its exit rule
                # belongs on the delimiter we now consume.
                region['end'] = '(?:%s|(?=$))' % exit_rx
                sc = self.exit_scope(dests[-1])
                if sc:
                    region['endCaptures'] = {'0': {'name': sc}}
                # Consuming the delimiter here also consumed the destination's
                # hand-off: `expr_eat_string` closes the quote with
                # `set: expr_wants_operator`, and that continuation never ran,
                # so `?"A";M;"B"` flagged everything after the string invalid.
                # Re-enter it as a sibling after the region so the rest of the
                # statement is parsed as an expression again.
                cont = self.exit_target(dests[-1])
                if cont and cont in self.contexts:
                    # The destination's own exit rule is now redundant -- we
                    # consume that delimiter -- and actively harmful: as a
                    # `set:` it converts to a begin/end region that reopened on
                    # the closing quote and swallowed the rest of the line, so
                    # `?"A";M;"B"` still errored. Use a body-only copy inside.
                    body = self.intern_body_only(dests[-1])
                    inner = dict(region)
                    inner['patterns'] = [{'include': '#' + body}]

                    # The continuation replaced the statement context via
                    # `set:`, so in Sublime that statement is still on the stack
                    # underneath and keeps handling its own punctuation -- the
                    # ';' between PRINT items is cmd_print's, not the
                    # expression's. Nesting hides that, and the continuation's
                    # syntax_error fallback then claimed `;M;"B"`. Emit the
                    # continuation WITHOUT its fallback so unhandled punctuation
                    # falls through to the enclosing statement, as it does in
                    # Sublime.
                    # A begin-less wrapper is NOT a sequence: it spliced the
                    # continuation's rules into the enclosing context as live
                    # alternatives, so expr_wants_operator's
                    # `{{var}}|{{flt}}|(` -> syntax_error beat the real
                    # variable rule and `IF A THEN 20` flagged `A` invalid.
                    # Open a real region on the string's own opener instead,
                    # ending when the continuation stops applying (its
                    # `match: '' / pop`). applyEndPatternLast lets the string
                    # claim the opening quote before that zero-width end runs.
                    cont_nf = self.intern_no_fallback(cont)
                    region = {
                        'begin': '(?=%s)' % inner['begin'],
                        'end': self.end_condition(cont)[0],
                        'applyEndPatternLast': 1,
                        'patterns': [inner, {'include': '#' + cont_nf}]}
        return region

    def is_included(self, ctx_name):
        """True if some other context `include:`s this one. A \\G-anchored
        wrapper only makes sense where the context is entered as a region;
        spliced in as a mixin, \\G would claim tokens its host owns (GOTO)."""
        for rules in self.contexts.values():
            for r in rules or []:
                if isinstance(r, dict) and r.get('include') == ctx_name:
                    return True
        return False

    def start_patterns(self, ctx_name, _seen=None, pops=True):
        """Every non-empty, non-pop match reachable in `ctx_name` (via include)."""
        seen = set() if _seen is None else _seen
        if ctx_name in seen:
            return []
        seen.add(ctx_name)
        out = []
        for r in self.contexts.get(ctx_name) or []:
            if not isinstance(r, dict):
                continue
            inc = r.get('include')
            if isinstance(inc, str):
                out += self.start_patterns(inc, seen, pops)
                continue
            if not pops and r.get('pop'):
                continue
            m = r.get('match')
            if isinstance(m, str) and m not in ('', '$', '\\s+'):
                m = fix_regex(expand_vars(m, self.variables))
                out.append(strip_leading_optional_ws(m)[0])
        return out

    def has_delimiter_error(self, ctx_name):
        """Context's OWN first rules say "statement ended too early": a
        `set: syntax_error` whose match can hit ':' or end of line. Only
        these regions get applyEndPatternLast -- applied broadly it lets
        zero-width body rules re-open at EOL forever."""
        for r in self.contexts.get(ctx_name) or []:
            if not isinstance(r, dict) or 'match' not in r:
                continue
            if targets_of(r) == ['syntax_error'] and \
                    (':' in r['match'] or '$' in r['match']):
                return True
        return False

    def error_scope(self):
        for r in self.contexts.get('syntax_error') or []:
            if isinstance(r, dict) and r.get('meta_scope'):
                return expand_vars(r['meta_scope'], self.variables)
        return 'invalid.error.syntax.ataribasic'

    def requires_more(self, dests):
        if len(dests) != 1 or not isinstance(dests[0], str) \
                or dests[0] == 'syntax_error':
            return False
        rules = [r for r in self.contexts.get(dests[0]) or []
                 if isinstance(r, dict) and ('match' in r or 'include' in r)]
        if any('include' in r or r.get('pop') for r in rules):
            return False
        return any(r.get('match') == '' and targets_of(r) == ['syntax_error']
                   for r in rules)

    def bracket_closer(self, ctx_name):
        """(rule, continuation) if `ctx_name` is a bracket body: it is not
        one-shot and has a rule matching a lone ')' or ']' that `set:`s a
        single named continuation context."""
        if self.is_oneshot(ctx_name):
            return None
        for r in self.contexts.get(ctx_name) or []:
            if not isinstance(r, dict):
                continue
            if r.get('match') in ('\\)', '\\]') and isinstance(r.get('set'), str):
                return r, r['set']
        return None

    def bracket_region(self, open_rx, open_scope, open_caps, body, closer):
        r, cont = closer
        close_rx = fix_regex(expand_vars(r['match'], self.variables))
        inner_name = body + '__noclose'
        if inner_name not in self.contexts:
            self.contexts[inner_name] = [x for x in self.contexts[body]
                                         if x is not r]
        b = {'begin': open_rx,
             # Pop ON the closer. `$` too: an unclosed bracket must not
             # carry the region into the next line (its own error rule
             # has already painted the rest of the line).
             'end': '(%s)|(?=$)' % close_rx,
             'patterns': [{'include': '#' + inner_name}]}
        caps = dict(open_caps) if open_caps else {}
        if open_scope:
            caps.setdefault('0', {'name': open_scope})
        if caps:
            b['beginCaptures'] = caps
        if r.get('scope'):
            b['endCaptures'] = {'1': {'name': expand_vars(r['scope'], self.variables)}}
        for x in self.contexts.get(body) or []:
            if isinstance(x, dict) and x.get('meta_content_scope'):
                b['contentName'] = expand_vars(x['meta_content_scope'], self.variables)
        cont_end, _ = self.end_condition(cont)
        return {'begin': '(?=%s)' % open_rx,
                # Try the continuation's rules first (end last), so after
                # the closer an operator is taken; anything else closes A.
                'end': '(?!\\G)(?<=\\S)|' + cont_end,
                'applyEndPatternLast': 1,
                'patterns': [b, {'include': '#' + cont}]}

    def is_oneshot(self, ctx_name, _seen=None):
        """True if every rule reachable in `ctx_name` transitions (set/pop).

        Such a context is a STATE, not a loop: it makes one decision and is
        replaced. Includes are followed; the bare whitespace skip is ignored.
        """
        seen = set() if _seen is None else _seen
        if ctx_name in seen:
            return True
        # A literal span (string/comment body) is not a decision state even
        # if its rules are all pops: it consumes text until its terminator.
        # Treating quoted_string as one-shot closed every string after its
        # first character (`L."D:THIS.BAS"` split at the colon).
        if _seen is None and not self.ends_at_delimiter(ctx_name) \
                and ctx_name != 'code_line':
            return False
        seen.add(ctx_name)
        found = False
        for r in self.contexts.get(ctx_name) or []:
            if not isinstance(r, dict):
                continue
            inc = r.get('include')
            if isinstance(inc, str):
                if not self.is_oneshot(inc, seen):
                    return False
                continue
            if 'match' not in r:
                continue
            if r.get('set') is not None or r.get('pop'):
                found = True
                continue
            if r['match'] == '\\s+':
                continue
            return False          # plain match or push: a loop
        return found

    def intern_no_fallback(self, ctx_name):
        """`ctx_name` minus its catch-all error span.

        Used where Sublime would have fallen through to a context still on the
        stack below. Keeping the fallback here would claim text that the
        enclosing statement owns.
        """
        name = '%s__nofallback' % ctx_name
        if name in self.contexts:
            return name
        kept = [r for r in (self.contexts.get(ctx_name) or [])
                if not (isinstance(r, dict) and r.get('match') == ''
                        and targets_of(r) == ['syntax_error'])]
        self.contexts[name] = kept
        return name

    def intern_body_only(self, ctx_name):
        """`ctx_name` without the exit rule, for use inside a bounded region."""
        name = '%s__body' % ctx_name
        if name in self.contexts:
            return name
        kept = [r for r in (self.contexts.get(ctx_name) or [])
                if not (isinstance(r, dict) and r.get('set')
                        and r.get('match') not in (None, '', '$', '.'))]
        self.contexts[name] = kept
        return name

    def exit_target(self, ctx_name):
        """Context the destination hands off to when it closes."""
        for r in self.contexts.get(ctx_name) or []:
            if not isinstance(r, dict) or not r.get('set'):
                continue
            m = r.get('match')
            if not m or m in ('', '$', '.'):
                continue
            t = targets_of(r)
            return t[-1] if t and isinstance(t[-1], str) else None
        return None

    def exit_scope(self, ctx_name):
        """Scope the destination applies to its own closing delimiter."""
        for r in self.contexts.get(ctx_name) or []:
            if not isinstance(r, dict) or not (r.get('pop') or r.get('set')):
                continue
            m = r.get('match')
            if not m or m in ('', '$', '.'):
                continue
            s = r.get('scope')
            return expand_vars(s, self.variables) if s else None
        return None

    def exit_pattern(self, ctx_name):
        """Regex for the delimiter on which `ctx_name` stops applying.

        Only a rule that matches the delimiter ALONE qualifies. One that also
        consumes the body on its way out (`[^"]*(")`) would match at the very
        start of the region and close it before anything was scoped, so such a
        context is left unbounded rather than mis-bounded.
        """
        for r in self.contexts.get(ctx_name) or []:
            if not isinstance(r, dict):
                continue
            if not (r.get('pop') or r.get('set')):
                continue
            m = r.get('match')
            if not m or m in ('', '$', '.'):
                continue
            if re.search(r'\[\^[^\]]*\]\s*[*+]', m):
                return None
            return fix_regex(expand_vars(m, self.variables))
        return None



    def intern_sequence(self, inner, outer):
        """A context that runs `inner`, then the `outer` chain after it pops.

        Sublime's stacked push is sequential, but TextMate only nests. So the
        continuation is spliced INSIDE each of inner's regions: once one of
        them closes, the outer contexts become reachable, and never before.
        A context with no regions to nest into can only be an alternative, so
        fall back to plain sibling includes there.
        """
        key = (inner,) + tuple(outer)
        if key in self._seq:
            return self._seq[key]

        name = '%s__then__%s' % (inner, '_'.join(outer))
        self._seq[key] = name

        src = self.contexts.get(inner) or []
        cont = [{'include': o} for o in outer]

        # The continuation goes AFTER the inner context's own rules, as
        # siblings. TextMate tries patterns in order at each position, so while
        # the inner context still matches (the '(' of an argument list) it
        # wins; once it no longer can -- the parens have closed and we are
        # looking at '+' -- the continuation is what applies. That reproduces
        # "inner first, then outer" without nesting, which mattered because
        # nesting the continuation inside inner's regions stole their `end` and
        # left the closing paren unconsumed.
        #
        # The inner context's catch-all fallback must not sit between them: it
        # would claim everything before the continuation ever got a turn, which
        # is what flagged `+256*PEEK(A+N1)` invalid. Drop it here; the
        # continuation's own fallback still reports genuine errors.
        keep = [r for r in src
                if not (isinstance(r, dict) and r.get('match') == ''
                        and targets_of(r) == ['syntax_error'])]
        self.contexts[name] = keep + cont
        return name

    def meta_scope_of(self, ctx_name):
        for r in self.contexts.get(ctx_name) or []:
            if isinstance(r, dict) and r.get('meta_scope'):
                return expand_vars(r['meta_scope'], self.variables)
        return None

    def content_scope_of(self, ctx_name):
        for r in self.contexts.get(ctx_name) or []:
            if isinstance(r, dict) and r.get('meta_content_scope'):
                return expand_vars(r['meta_content_scope'], self.variables)
        return None

    def conv_context(self, name, rules):
        if not isinstance(rules, list):
            return None
        entry, pats = {}, []
        kinds = []                         # parallel to pats: 'set' / 'err' / ''
        for r in rules:
            if isinstance(r, dict):
                if r.get('meta_scope'):
                    entry['name'] = expand_vars(r['meta_scope'], self.variables)
                if r.get('meta_content_scope'):
                    entry['contentName'] = expand_vars(
                        r['meta_content_scope'], self.variables)
            c = self.conv_rule(r)
            if c:
                pats.append(c)
                if c.get('_delim_err'):
                    kinds.append('err')
                elif isinstance(r, dict) and r.get('set') is not None \
                        and r.get('match'):
                    kinds.append('set')
                else:
                    kinds.append('')
            # `match: '' / set: D` where D cannot end a statement (no pop, no
            # include, only `'' -> syntax_error` as its way out): reaching
            # ':' or EOL here is an error (`LET A`, `A`). The guarded
            # fallback never fires on ':'/EOL, so add the error explicitly.
            if isinstance(r, dict) and r.get('match') == '' \
                    and self.requires_more(targets_of(r)):
                pats.append({'match': '\\s*(?::.*|$)', 'name': self.error_scope(),
                             '_delim_err': True})

        # In Sublime a `match: '' / set: syntax_error` fallback is reached only
        # when every rule ABOVE it failed at this position, and the context is
        # then gone -- `required_args` matches '(' and is replaced. A TextMate
        # include has neither property: the fallback stays live as a sibling
        # forever, so after `PEEK(A)` closed its parens the SAME required_args
        # include caught the following '+' and flagged `+256*PEEK(A+N1)`
        # invalid. Guard it with a negative lookahead for the patterns that
        # precede it, which restores "only if nothing else here applies".
        for i, p in enumerate(pats):
            if p.get('begin') != '(?=[^\\s:])':
                continue
            earlier = [q.get('begin') or q.get('match') for q in pats[:i]]
            earlier = [e for e in earlier if e and e != '\\s+']
            if earlier:
                guard = '|'.join('(?:%s)' % e for e in dict.fromkeys(earlier))
                # Keep the positive lookahead: a bare negative lookahead also
                # succeeds at end of line, where it reopened a zero-width
                # region that TextMate then carried into the NEXT line --
                # every line after the first fell into syntax_error.
                p['begin'] = '(?=[^\\s:])(?!%s)' % guard

        # A LOOP context that leaves by `set:` on a real token (two_exprs:
        # `,` -> one_expr) and has a "statement ended too early" error.
        # In Sublime the set REPLACES the context, so the error applies only
        # BEFORE that token; nested in TextMate it stayed live after the
        # set-child finished, and `POKE 1,2:` would go red. Scope the error
        # and the context's other rules to a \G-anchored region that runs
        # from the context's start up to the set token:
        #   `POKE 10:` -> error;  `POKE 1,2:` -> fine.
        # Its end is tried first but never matches ':', so the error rule
        # (first inside) still claims a premature delimiter.
        empty_set = any(isinstance(r, dict) and r.get('match') == ''
                        and r.get('set') is not None for r in rules)
        if 'err' in kinds and 'set' in kinds and not empty_set \
                and not self.is_oneshot(name) and not self.is_included(name):
            sets = [p for p, k in zip(pats, kinds) if k == 'set']
            rest = [p for p, k in zip(pats, kinds) if k != 'set']
            rest.sort(key=lambda p: not p.get('_delim_err'))   # errors first
            for p in rest:
                p.pop('_delim_err', None)
            stop = '|'.join('(?:%s)' % p['begin'] for p in sets)
            pats = [{'begin': '\\G', 'end': '(?=%s)|(?=$)' % stop,
                     'patterns': rest, '_delim_err': True}] + sets
        entry['patterns'] = pats
        return entry


# Contexts that hold a LIST of statements, where ':' separates siblings rather
# than ending the context. Statement regions inside them end with a lookahead at
# ':' (see end_condition), so these must consume and scope the delimiter.
STATEMENT_LIST_CONTEXTS = {'code_line'}


def convert(src, scope_name, display_name):
    with open(src, encoding='utf-8') as f:
        doc = yaml.load(f, Loader=SublimeLoader)

    variables = doc.get('variables', {}) or {}
    for _ in range(8):                        # variables may reference variables
        variables = {k: expand_vars(v, variables) for k, v in variables.items()}

    contexts = doc.get('contexts', {}) or {}
    conv = Converter(contexts, variables)

    # Sublime's prototype rules apply inside every context that doesn't opt out.
    # We model only the whitespace skip (the `$` pop becomes each region's end).
    proto_ws = [r for r in (contexts.get('prototype') or [])
                if isinstance(r, dict) and not r.get('pop') and 'match' in r]

    repository = {}
    # Anonymous inline contexts are interned into `contexts` while their
    # enclosing rule is converted, so iterate a worklist rather than
    # `contexts.items()` (mutating a dict during iteration raises).
    pending = [n for n in contexts if n != 'prototype']
    done = set()
    while pending:
        name = pending.pop(0)
        if name in done:
            continue
        done.add(name)
        rules = contexts[name]
        entry = conv.conv_context(name, rules)
        pending.extend(n for n in contexts
                       if n not in done and n not in pending and n != 'prototype')
        if not entry:
            continue
        # Keep a context that only carries meta_content_scope (e.g. cmd_rem is
        # just `meta_content_scope: comment` + an EOL pop). Dropping it would
        # lose the scope for the whole region's contents.
        if not entry.get('patterns') and not entry.get('contentName') \
                and not entry.get('name'):
            continue
        # Prepend the whitespace skip so leading blanks are consumed before the
        # context's own rules compete -- the behaviour Sublime gets for free.
        opts_out = any(isinstance(r, dict) and
                       r.get('meta_include_prototype') is False for r in rules)
        if not opts_out:
            for r in reversed(proto_ws):
                c = conv.conv_rule(r)
                if c:
                    entry['patterns'].insert(0, c)
            # ...except a "statement ended too early" error, which starts on
            # the blank itself and must see it before the skip eats it.
            errs = [p for p in entry['patterns'] if p.get('_delim_err')]
            if errs:
                entry['patterns'] = errs + [p for p in entry['patterns']
                                            if not p.get('_delim_err')]
        for p in entry['patterns']:
            p.pop('_delim_err', None)

        # Statement regions end by LOOKING at ':' without consuming it, so the
        # statement-list context must consume and scope the delimiter itself --
        # otherwise nothing advances past it and every statement after the
        # first stays unscoped (`GR. 8:COLOR 1` coloured only `GR. 8`).
        if name in STATEMENT_LIST_CONTEXTS:
            entry['patterns'].append({
                'match': ':',
                'name': 'support.token.delimiter.statement.ataribasic',
            })
        repository[name] = entry

    main = repository.get('main', {}).get('patterns')
    if not main:
        main = [{'include': '#' + k} for k in repository]

    grammar = {
        '$schema': 'https://raw.githubusercontent.com/martinring/tmlanguage/master/tmlanguage.json',
        'name': display_name,
        'scopeName': scope_name,
        'patterns': main,
        'repository': repository,
    }
    if doc.get('file_extensions'):
        grammar['fileTypes'] = doc['file_extensions']
    return grammar


def count_forms(grammar):
    n = {'begin': 0, 'match': 0, 'include': 0}

    def walk(o):
        if isinstance(o, dict):
            for k in n:
                if k in o:
                    n[k] += 1
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(grammar)
    return n


def validate(grammar):
    """Catch structural faults the TextMate engine would accept but mis-apply.

    Notably a non-string `begin`/`end`/`match`: YAML turning bare `ON` into the
    boolean True produced `"begin": true`, which matches EVERY position and
    silently swallowed the statement keywords.
    """
    problems = []

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ('begin', 'end', 'match') and not isinstance(v, str):
                    problems.append(f'{path}/{k} is {type(v).__name__} ({v!r}), '
                                    f'expected a regex string')
                walk(v, f'{path}/{k}')
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f'{path}[{i}]')

    walk(grammar, '')
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--scope', default='source.ataribasic')
    ap.add_argument('--name', default='Atari BASIC')
    a = ap.parse_args()

    g = convert(a.src, a.scope, a.name)

    problems = validate(g)
    if problems:
        print(f'{a.src}: REFUSING to write -- {len(problems)} structural problem(s):',
              file=sys.stderr)
        for p in problems:
            print(f'  {p}', file=sys.stderr)
        return 1

    with open(a.dst, 'w', encoding='utf-8') as f:
        json.dump(g, f, indent=2)
        f.write('\n')

    forms = count_forms(g)
    print(f'{a.src} -> {a.dst}')
    print(f'  scopeName : {g["scopeName"]}')
    print(f'  top-level : {len(g["patterns"])} patterns')
    print(f'  repository: {len(g["repository"])} contexts')
    print(f'  regions   : {forms["begin"]} begin/end, '
          f'{forms["match"]} match, {forms["include"]} include')
    return 0


if __name__ == '__main__':
    sys.exit(main())
