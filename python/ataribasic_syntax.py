#!/usr/bin/env python3
#
# ataribasic_syntax.py
#
# Parser to tokenize and validate Atari BASIC
# based on the original Atari BASIC source code.
#

from ataribasic import *
import functools
from functools import partial
import signal

DEBUG_CODE = False  # Enable for temporary tokenizer tracing during investigation

# Temporary timeout investigation: keep prints behind DEBUG_CODE so they can be removed cleanly.
# TODO after investigation: delete this block and all DEBUG_CODE branches once ALLTOKEN.LST runs without hangs.

CIX = 0
COX = 0
DIRFLG = False
OUTBUFF = []
LBUFF = ""

MAXCIX = 0 # Value of CIX where a syntax error was detected
SVVNTP = 0 # Saved VNT Pointer to restore in case of a syntax error
SVVVTE = 0 # Number of new vars in the current line

# Timeout handler for debugging
def timeout_handler(signum, frame):
    raise TimeoutError("Parser timed out - possible infinite recursion")

# Set a timeout for debugging purposes
signal.signal(signal.SIGALRM, timeout_handler)

# EXPAND / CONTRACT
# Used to expand and contract the Statement Table, Variable Tables, etc.
def util_EXPAND(offset, size):
    pass
def util_CONTRACT(offset, size):
    pass

# EXECNL - Execute Next Line
def util_EXECNL():
    # Set up state via SETLN1
    # Fall through (so to speak) to EXECNS
    pass

# EXECNS - Execute Next Statement
def util_EXECNS():
    # - If BREAK is pressed do STOP
    # - Get offset to next statement line NXTSTD
    # - At end of line? Done if direct command (i.e., 32768).
    # - Go to next line via GNXTL.
    # - Test for end of statement table via TENDST.
    # - Execute Next Line via EXECNL, of return up to loop.
    pass

# GNXTL - Advance STMCUR by A bytes
def util_GNXTL(A):
    STMCUR += A

# GETSTMT - Get Statement in Statement Table
# - Save current line address STMCUR in SAVCUR.
# - Search for statement that has line number TSLNUM.
# - Set STMCUR to point to it if found.
#   or to where it would go if not found.
# - Return True if not found, false if found.
def util_GETSTMT(TSLNUM):
    pass

# TENDST - Test End of Statement Table
def util_TENDST():
    pass

# TESTBRK - Test for Break
def util_TESTBRK():
    # We can get as low level as this:
    #Y = 0xFF if BRKBYTE == 0 else 0x00
    #A = Y
    # But the effective logic is:
    return BRKBYTE == 0

# SKIPBLANK ($DBA1) - Skip blanks in the parser input buffer.
def util_SKIPBLANK():
    while LBUFF[CIX] in (' ', '\t'):
        CIX += 1

# SETCODE ($A2C8) - Set a code in the OUTBUFF and increment COX
def util_SETCODE(T):
    OUTBUFF[COX] = T
    COX += 1
    pass

# SEARCH ($A462)
# - Match the input to a statement in the Statement Name Table
#   - Unrecognized statements are assumed to be Implied Let.
# - Set STENNUM to the statement number, if found.
# - Add the statement token to OUTBUFF. Remember this token's position (address) in SRCADR.
#
def util_SEARCH():
    pass


#
# Syntax Primitives
#

#
# Program Editor Vars
#
# INBUFF ($F3-$F4) - The pointer to LBUFF used for 6502 indexing. Not used here.
# LBUFF ($580) - Input Buffer for the text line. (A line over 128 bytes will step on Page 6.)
# OUTBUFF - Output Buffer for a tokenized line that may be inserted into the Statement Table.
# CIX ($F2) - Index into the user input line in the line buffer (LBUFF)
# COX ($94) - Index into the output buffer (OUTBUFF)
# DIRFLG ($A6) - Direct input line flag (input line has no number, use pseudo-line 32768)
# MAXCIX ($9F) - Value of CIX where a syntax error was detected.
# SVVNTP ($AD-$AE) - Saved VNT Pointer to restore in case of a syntax error.
#                    More memory-efficient on 6502 to add vars and revert on error, rather than
#                    maintain a buffer of new vars to add when the line is done processing.
# SVVVTE ($B1) - Number of new vars in the line. Init to 0 before processing the line.
#

#
# The Line Parsing Process
#
# A Program is composed of numbered lines containing one or more statements (STMT).
# A statement consists of a full or abbreviated command followed by one or more parameters.
#
# Line Parsing:
# - Skip over blanks
# - Process the Line Number.
#   The line number is processed as a float before rounding to 16 bit little-endian integer.
#   Line int is stored in TSLNUM and added to OUTBUFF. No line number? Set DIRFLG and line 32768.
# - Add a placeholder byte in OUTBUFF for the offset to next line, to be populated later.
# - Do SKIPBLANKS then save CIX in STMSTRT to remember the start of the first statement.
#   This can be used later if there is a syntax error in the line.
# - Nothing but a line number? Delete the line with that number, if any, using util_CONTRACT.
#
# Statement Parsing:
# An input line consists of one or more statements. For each statement:
# - Init, storing COX in STMLBD. This index in OUTBUFF will contain the offset to next statement byte.
# - Add a placeholder byte to OUTBUFF for the offset to next statement, to be populated later.
# - Recognize the Statement Name
#   - Skip input blanks and process the statement name with util_SEARCH.
#   - A bad statement/variable name is a syntax error. (It must start with a letter.)
# - Transfer control to the pre-compiler, which:
#   - Places the appropriate tokens in OUTBUFF,
#   - Increments CIX, COX to next locations.
#   - Returns a flag to indicate if there was an error.
# - If a Syntax Error is detected:
#   - The editor gets back a flag indicating the error.
#   - MAXCIX will contain the CIX offset into LBUFF where the error was detected. Invert this character.
#   - Set the flag 0x40 in DIRFLG to indicate the error. (0x80 indicates Direct Statement.)
#   - Set CIX = STMSTRT
#   - Set STMLBD to indicate the location of the first Statement Length byte in OUTBUFF.
#   - Set COX to the index of the first statement token in OUTBUFF, replace with cERR.
#   - Copy the entire line after the line number from INBUFF to OUTBUFF. (Later OUTBUFF will be inserted into the Statement Table.)
#
# - Final Statement Processing (for both good and erroneous lines):
#   - Set OUTBUFF[STMLBD] = COX to populate the Next Statement offset.
#   - Check whether the next character is CR.
#     - If not, go on to process the next statement.
#     - If so...
#       - Set OUTBUFF[2] = COX to populate the Next Line Offset.
#       - Insert or Replace the line in the Statement Table.
#
# Inserting / Adding to the Statement Table
# - Determine where in the Statement Table the new line will go:
#   - A new line will always expand the buffer, except when it's the last line.
#   - A replacement line will expand or contract the Statement Table space as-needed.
#   - (Our Python implementation of the Statement Table is an array indexed by line number,
#     and we only synthesize a contiguous buffer for SAVE.)
# - Copy OUTBUFF to the allocated space in the Statement Table.
#
# Line Wrap-up
# After the line has been added to the Statement Table the editor checks DIRFLG for the
# syntax error indicator (0x40).
#
# Error Wrap-up
# - If there is an error:
#   - Any variables added by the line are removed. i.e., The VNT and VVT are contracted.
#   - The editor lists the line, which will show "ERROR -  " and the raw text of the line
#     with the character at the error index inverted.
#
# Handling Correct Lines
# - If the line was syntactically okay, the editor checks DIRFLG flag 0x80 for immediate input.
# - If the line was not immediate then the editor continues to accept input.
# - An immediate line will have been assigned line # 32768 and added to the end of the table.
#   During editing STMCUR ($8A-$8B) points to this line.
# - The editor transfers control to Execution Control ($A95F) to execute the direct statement,
#   which may have a GOTO, GOSUB, TRAP, etc. that jumps into the stored program.
#

########################
#       Syntaxer       #
########################

#
# Syntax Tables for the Atari BASIC Syntaxer
#
# Each command has an expected syntax that we can enforce using the 81 ABML rules
# tables below.
#
# At bottom the rulesets state exactly what kind of tokens are allowed at each
# successive position in a statement after the command token. As soon as an allowed
# token's pattern is identified it passes the rule being tested.
#
# Development Plan:
# - Fill in all the rules as ABML.
# - Build the ABML processor that emulates the original validation + tokenizing process
#   using a central loop that does all the logic and dispatching. The loop can check if a
#   rule element is a function ref, a ref to another rule, a rule token, an operator/function
#   token, etc. It would maintain the expression stack, "back up" when needed, modify chars,
#   and deal with rules calling sub-rules and recursing.
# - Also translate the 81 rulesets into Python function calls using language features to
#   accomplish the same process. e.g., ABML "cUSR" becomes a direct call to self.f_SRCONT(cUSR) to
#   check if the input string matches the name of that function, and if it does, advance CIX,
#   and insert the token into the OUTBUFF, and return 'pass'. ABML "cPLUS CHNG cUPLUS" becomes
#   self.f_CHNG(cPLUS, cUPLUS) to check for match and apply the change.
#

#
# Atari BASIC Meta-Language (ABML) Commands
# ABML is derived from BNF.
#  - 0x00..0x0F : Commands
#  - 0x10..0x7F : Operator and Function Tokens
#  - 0x80..0xBF : Relative Non-Terminal Vectors - shorter than ANTV
#
#  - JS(N) Make a Relative Non-Terminal Vector for rule N
#  - AD(N) Make a Word Address for rule N following an ABML Command such as ANTV
#
# Validating syntax consists of checking the input against the rules,
# where each rule reports pass/fail. Higher level rules use lower level rules.
# Rules are similar to regexes in being used for matching and capturing.
# When one rule fails, keep trying the next 'OR' rule, until all rules have failed.
# Each symbol in the input must pass before the next is checked. The first character
# being checked or changed in a rule is the last character added to OUTBUFF, so, e.g.,
# a USR rule needs to match a cUSR token. The deepest sort of rule checks for a valid
# character, such as a letter, digit, or operator.
# When the "or nothing" rule is passed we do not advance to the next rule symbol.
#
# Backing Up
# Sometimes we need to back up over symbols already processed so we
# can check the next rule after a fail. The rule caller must remember
# the current position before checking sub-conditions so it can back up to
# check the next rule.
#
# Location of Syntax Error
# Highlight the character after the rightmost valid symbol during various back-ups.
#

kANTV = 0x00 # Absolute Non-Terminal Vector (ANTV) to sub-call another rule
kESRT = 0x01 # External Subroutine Call (ESRT) to call a handler for more complex rules
kOR   = 0x02 # ABML or
kRTN  = 0x03 # (aka <END>) Return, marks the end of an ABML rule. Return pass or fail.
kVEXP = 0x0E # (aka <EXP>) Expression Non-Terminal Vector. Shorthand for ANTV AD(EXP)
kCHNG = 0x0F # Change Last Token to X. e.g., to rectify '=' as assign or compare.

# For this adaptation we'll use direct references to other rules in place of ANTV, VEXP, and ESRT.
# So 'ANTV.AD(RULE)' and 'ESRT.AD(RULE)' become 's_RULE' and 'VEXP' becomes 's_EXP'.
# When converting to code use 'f_RULE()' and 'f_EXP()',
#   'OR' => 'or', 'RTN' => 'return False'. Empty rule 'OR RTN' permits NADA.

#
# As parts of rules are tested we move cix and cox forward.
# So we need to save cix and cox in a stack. To do this we'll use a wrapper.
# On success the new cix, cox will be left as they are. On fail they will be restored.
def rule(fn):
    def wrapper(*args, **kwargs):
        return lambda: fn(*args, **kwargs)
    return wrapper

class Syntaxer:


    # Best idea seems to be to use callables
    # and automatically turn them into lambdas
    #return (
    #    p.attempt(
    #        f_CHNG(cLPRN, cALPRN),
    #        f_EXP,
    #        f_SRCONT(cRPRN),
    #        f_NOP
    #    )
    #    or
    #    p.attempt(
    #        f_UNARY,
    #        f_EXP
    #    )
    #    or
    #    p.attempt(
    #        f_NV,
    #        f_NOP
    #    )
    #)
    def attempt(self, *funcs):
        tokenized_snap = (self.index, len(self.tokenized))
        prog_snap = None
        if self.program is not None:
            prog_snap = self.program._snapshot()
        for f in funcs:
            if not f():
                self.restore(tokenized_snap)
                if prog_snap is not None:
                    self.program._restore_snapshot(prog_snap)
                return False
        return True

    def snapshot(self):
        """Save parser position for backtracking. Returns (index, tokenized_len)."""
        return (self.index, len(self.tokenized))

    def restore(self, state):
        """Restore parser position from a snapshot."""
        # ROM _SETCODE (ataribas.asm ~924) keeps MAXCIX as the high-water mark
        # of CIX, updated as each alternative backtracks:
        #     if (cix > maxcix) maxcix = cix;
        # It marks how far the parse got before failing, which is where the
        # syntax error is reported and where the ERROR line inverts a
        # character. restore() is our single backtracking choke point.
        if self.index > self.maxcix:
            self.maxcix = self.index
        self.index, tlen = state
        del self.tokenized[tlen:]

    def util_SKIPBLANK(self):
        while self.index < len(self.lbuff) and self.lbuff[self.index] in (ord(' '), ord('\t')):
            self.index += 1

    # Append cCR end-of-line marker to tokenized output
    def f_append_cCR(self):
        self.tokenized.append(cCR)
        return True

    # Search ONT Operator Name Table
    # SRCONT: Check if the current symbol matches the terminal symbol represented by the current operator token (according to the Operator Name Table).
    #         For example, if the rule specifies cCHR this checks whether the input matches "CHR$".
    #         On match, add the token to OUTBUFF and return 'pass'. Else return 'fail'.
    #         The original AtariBASIC syntaxer most likely, on fail, resets the input index back to the start of
    #         the test logic before the "or" that follows.
    #         The original syntaxer would have had logic to skip ahead to next rule following _OR,
    #         whereas we let Python go there. So each rule that could be tested must do the same.
    def f_SRCONT(self, ltoken, rtoken=None):
        self.util_SKIPBLANK()
        token, next_index = search_operator_name_table(self.lbuff, self.index)
        if DEBUG_CODE: print(f"DEBUG f_SRCONT: index={self.index}, lbuff[{self.index}]={chr(self.lbuff[self.index]) if self.index < len(self.lbuff) else 'EOF'}, token={token}, next_index={next_index}")
        # If the input matches the operator or function, store it and advance input index
        if token == ltoken:
            append_val = rtoken if rtoken else ltoken
            self.tokenized.append(append_val)
            self.index = next_index
            if DEBUG_CODE: print(f"DEBUG f_SRCONT: matched, new index={self.index}")
            return True
        # CNFNP is a CLASS marker, not a literal token. SRCONT in the ROM
        # (ataribas.asm ~1108-1119): if the required code is CNFNP and the
        # found code is >= CNFNP, it succeeds and emits the FOUND token --
        # that is how every numeric function (ATN, COS, PEEK, SIN, RND, FRE,
        # EXP, LOG, CLOG, SQR, SGN, ABS, INT, ...) matches one rule byte.
        if ltoken == cNFNP and token is not None and token >= cNFNP:
            self.tokenized.append(token)
            self.index = next_index
            return True
        # Don't advance index here - let the caller handle state restoration via snapshot/restore
        return False

    # CHNG: Match a token, change it to the one that fits the context
    def f_CHNG(self, ltoken, rtoken):
        return self.f_SRCONT(ltoken, rtoken)

    # TNVAR: Examine the current source symbol for a numeric variable. Array var names end with '('.
    #        On success create the var, put the var token in OUTBUFF, return 'pass'. Else return 'fail'.
    def f_TNVAR(self):
        self.util_SKIPBLANK()
        if self.index >= len(self.lbuff):
            return False
        c = self.lbuff[self.index]
        ch = chr(c).upper()
        if not ('A' <= ch <= 'Z'):
            return False
        start = self.index
        self.index += 1
        while self.index < len(self.lbuff):
            ch = chr(self.lbuff[self.index]).upper()
            if 'A' <= ch <= 'Z' or '0' <= ch <= '9':
                self.index += 1
                continue
            break

        # Reject variable names that end with '$' - those are string variables!
        # (Check the source char AFTER the name; the '$' has not been consumed yet.)
        #
        # MUST restore self.index before returning False. A failing rule may
        # not leave the input position advanced, or the next alternative
        # (f_TSVAR) starts scanning PAST the name it needs to read. That is
        # what broke every multi-character string variable: for `?AB$` this
        # left index at the '$', f_TSVAR saw no leading letter and failed, and
        # the statement consumed nothing. (`?A$` happened to survive because a
        # later retry re-entered at the right offset.)
        if self.index < len(self.lbuff) and self.lbuff[self.index] == ord('$'):
            self.index = start
            return False

        # NOTE: ESRT(_TNVAR) in ataribas.asm does NOT consume the following '('.
        # The '(' is handled by the caller (e.g. f_CHNG(cLPRN, cDLPRN) in f_NSMAT,
        # or f_CHNG(cLPRN, cALPRN) in f_NMAT for subscripts). We must leave it.
        #
        # BUT the '(' IS part of the VNT NAME. _TVAR (ataribas.asm ~1251) peeks
        # at the next char and, if it is '(', takes it into the name and sets
        # VT_ARRAY ($40) before searching the VNT. So scalar `B` and array `B(`
        # are TWO SEPARATE variables occupying two VNT slots.
        #
        # Registering the array as plain `B` merged the two entries, so every
        # variable declared after the first array was numbered one too low --
        # 40 of ALLTOKEN.ULST's 47 byte differences were exactly this, an
        # index off by one (0x82 where the reference has 0x83).
        #
        # Look ahead WITHOUT consuming: the token stream still needs the caller
        # to emit cALPRN/cDLPRN for the '('.
        name = self.lbuff[start:self.index].decode('ascii').upper()
        if self.index < len(self.lbuff) and self.lbuff[self.index] == ord('('):
            name += '('

        v_idx = self.program._create_numeric_variable(name)
        self.tokenized.append(0x80 | v_idx)
        return True

    # TSVAR: Examine the current source symbol for a string variable. String var names end in '$'.
    #        On success create the var, put the var token in OUTBUFF, return 'pass'. Else return 'fail'.
    def f_TSVAR(self):
        self.util_SKIPBLANK()
        if self.index >= len(self.lbuff):
            return False
        c = self.lbuff[self.index]
        ch = chr(c).upper()
        if not ('A' <= ch <= 'Z'):
            return False
        start = self.index
        self.index += 1
        while self.index < len(self.lbuff):
            ch = chr(self.lbuff[self.index]).upper()
            if 'A' <= ch <= 'Z' or '0' <= ch <= '9':
                self.index += 1
                continue
            break

        if DEBUG_CODE:
            print(f"DEBUG f_TSVAR: index={self.index}, lbuff[{self.index}]={chr(self.lbuff[self.index]) if self.index < len(self.lbuff) else 'EOF'}, lbuff={list(self.lbuff[self.index:self.index+10])}")

        if self.index >= len(self.lbuff) or self.lbuff[self.index] != ord('$'):
            if DEBUG_CODE: print(f"DEBUG f_TSVAR: FAIL - no '$' at index {self.index}")
            self.index = start
            return False
        # Capture the name length (start..index, inclusive of '$') BEFORE
        # consuming any '(' that follows, so the '(' doesn't count as name.
        name_len = self.index - start + 1
        self.index += 1

        # NO length limit. The ROM's _TSVAR/_TVAR (ataribas.asm) scans
        # alphanumerics until a non-name character and never checks a maximum
        # -- Atari BASIC variable names may be long (SP$, C1$, HEX$ are all
        # legal), exactly as the f_TNVAR twin already allows for numerics.
        # A `name_len > 2` rejection here failed EVERY string variable longer
        # than X$, which is 64 of UNTOKEN.ULST's 71 bad lines.

        if DEBUG_CODE:
            print(f"DEBUG f_TSVAR: SUCCESS - index={self.index}")

        # No '(' handling here, unlike f_TNVAR. _TVAR's string path does
        # `INY / BNE @TVOK2` -- an unconditional branch PAST the '(' test --
        # so a string name ends at the '$' and A$ and A$( are the SAME
        # variable, sharing one VNT entry.
        name = self.lbuff[start:self.index].decode('ascii').upper()
        v_idx = self.program._create_string_variable(name)
        self.tokenized.append(0x80 | v_idx)
        return True

    # TNCON and TSCON are implemented below as token primitives
    # When the stack is reset, actually reset, to $FF it erases all the accumulated JSRs so it can carry on in
    # the program main loop, which we know to be the top level. To do the same we might be able to use try/catch.

    # EIF : Continue with statement processing after IF ... THEN
    #       Reset stack, set statement length byte in OUTBUFF, continue at statement after THEN...
    #       This causes the Syntaxer to break out of the rule and go to the start of the statement
    #       processor where we get the Command token.
    def f_EIF(self):
        # ROM _EIF (ataribas.asm:1045):
        #     LDX #$FF / TXS          reset the stack
        #     LDA COX / LDY STMLBD / STA (OUTBUFF),Y
        #     JMP _XIF                and _XIF IS SYN1 (ataribas.asm:367)
        #
        # _EIF does NOT parse the IF body. It CLOSES the current statement --
        # writing the displacement into this statement's length byte -- and
        # jumps back to the top of the per-statement loop, so whatever follows
        # THEN is tokenized as an ordinary NEXT STATEMENT.
        #
        # That is why `IF A THEN B=1`, `IF A THEN RET.` and
        # `IF N$="" THEN RUN "D:M.BAS"` are all legal: the body is simply the
        # next statement, with no enumeration of which keywords may appear.
        #
        # This previously hand-rolled a ~24-keyword statement table, which is
        # pure invention -- any statement outside that list failed, and the
        # list could never be complete. All we must do here is emit the
        # statement terminator and succeed; tokenize_statement() then writes
        # the displacement (its SYNOK step) and tokenize_line()'s loop plays
        # the part of SYN1 for the body.
        self.util_SKIPBLANK()

        # There must actually BE a body after THEN.
        if self.index >= len(self.lbuff) or self.lbuff[self.index] == 0x9B:
            return False

        # Close this statement, exactly as _EIF does before jumping to SYN1.
        #
        # _EIF writes the length byte and jumps -- it emits NO terminator
        # token. The IF statement simply ENDS after cTHEN, and the body is the
        # next statement. Reference TESTMIXE.BAS line 40 confirms it:
        #
        #   2800 1b 0f 0780220e4001000000001b 1b 36802d0e40010000000016
        #                          cTHEN ^    ^ disp, no cEOS/cCR between
        #
        # Appending cCR here desynchronized the statement table: every IF line
        # gained a byte the ROM does not emit.
        return True

    # XDATA : Copy the rest of the line up to CR into OUTBUFF advancing CIX, COX, set the statement length, set line length, then go on to the next line.
    def f_XDATA(self):
        # Copy remaining characters up to CR, then terminate with a literal
        # CR (0x9B) -- NOT cCR/22.
        #
        # ROM _XDATA (ataribas.asm ~448) copies source bytes until it consumes
        # a CR, then calls _SETCODE with that CR, and `CR EQU $9B`
        # (sourcebook.inc:143). So a REM/DATA payload ends 0x9B while every
        # other statement ends cCR 0x16.
        #
        # This used to emit 22 because tokenize_statement() tested success with
        # `22 not in tokenized` and would reject the statement. That check now
        # accepts either terminator. REM/DATA always consume the rest of the
        # line, so they are necessarily the LAST statement -- the
        # multi-statement loop never has to pop their terminator.
        # First, skip any leading blanks
        self.util_SKIPBLANK()
        # Copy remaining bytes until end of buffer or EOS
        while self.index < len(self.lbuff):
            # Copy the byte to tokenized output
            self.tokenized.append(self.lbuff[self.index])
            self.index += 1
        # Append the literal CR (0x9B) the ROM stores for REM/DATA payloads
        self.tokenized.append(0x9B)
        return True

    # EREM : Reset the stack, copy the rest of the line up to CR into OUTBUFF advancing CIX, COX, set the statement length, set line length, then go on to the next line.
    #        In the original code EREM/EDATA jumps to 'XDATA' in middle of 'SYNTAX' where data copying up to CR happens just ahead of the statement/line loop logic.
    #        The nearest thing in our implementation is to update the core state machine.
    def f_EREM(self): return self.f_XDATA()
    f_EDATA = f_EREM

    # ========================================
    def f_NADA(self):
        return True

    # ========================================
    # Numeric Constant (TNCON) - Match a numeric literal and emit cTNCON + 6 BCD bytes
    # <NCON> = <DIGIT>+ | <DIGIT>+ . <DIGIT>* (E [+-] <DIGIT>+)?
    # ABML: ESRT.AD(TNCON) RTN

    def f_TNCON(self):
        """Match a numeric literal and emit cTNCON + 6 BCD bytes."""
        from ataribasic import encode_bcd

        # Save position for backtracking
        save_index = self.index

        # Skip leading whitespace (shouldn't be any at this point)
        while self.index < len(self.lbuff) and self.lbuff[self.index] in (32, 9):
            self.index += 1

        # Match digits before decimal point (if any)
        while self.index < len(self.lbuff) and chr(self.lbuff[self.index]).isdigit():
            self.index += 1

        # Match optional decimal point and digits after (if any)
        if self.index < len(self.lbuff) and chr(self.lbuff[self.index]) == '.':
            self.index += 1
            while self.index < len(self.lbuff) and chr(self.lbuff[self.index]).isdigit():
                self.index += 1

        # Match optional exponent (E or e followed by optional +/- and digits)
        if self.index < len(self.lbuff) and chr(self.lbuff[self.index]).lower() == 'e':
            self.index += 1
            if self.index < len(self.lbuff) and chr(self.lbuff[self.index]) in '+-':
                self.index += 1
            while self.index < len(self.lbuff) and chr(self.lbuff[self.index]).isdigit():
                self.index += 1

        # Check if we matched something
        if self.index == save_index:
            return False

        # Extract the numeric string and convert to float
        num_str = self.lbuff[save_index:self.index].decode('ascii')

        try:
            value = float(num_str)
        except ValueError:
            self.index = save_index
            return False

        # Encode as BCD and emit token + 6 bytes
        bcd = encode_bcd(value)

        # Append cTNCON token (14) and 6 BCD bytes
        self.tokenized.append(14)  # cTNCON
        for b in bcd:
            self.tokenized.append(b)

        return True

    # String Constant (TSCON) - Match a double-quoted string literal
    # <SCON> = " <CHAR>* " where CHAR is any byte except $9B and $22
    # ABML: ESRT.AD(TSCON) RTN

    def f_TSCON(self):
        """Match a double-quoted string literal and emit cTSCON + length + chars."""
        # Save position for backtracking
        save_index = self.index

        # Must start with double quote ($22)
        if self.index >= len(self.lbuff) or chr(self.lbuff[self.index]) != '"':
            return False

        self.index += 1  # Skip opening quote

        # Collect string characters until closing quote or $9B
        chars = []
        while self.index < len(self.lbuff):
            b = self.lbuff[self.index]

            # End of string marker ($9B) or closing quote ends the string.
            # Every other byte is literal -- including $00 and $01 (ATASCII
            # heart and left-tee), which programs use for graphics and for
            # machine code in strings. The ROM's string scan (_TSCON) has no
            # exceptions; skipping them truncated HOPPERG's strings.
            if b == 0x9B or chr(b) == '"':
                break

            chars.append(b)
            self.index += 1

        # Check if we found closing quote
        if self.index >= len(self.lbuff) or chr(self.lbuff[self.index]) != '"':
            self.index = save_index
            return False

        # Consume the closing quote
        self.index += 1

        # Emit cTSCON token (15) + length byte + string bytes
        self.tokenized.append(cTSCON)  # String constant token
        self.tokenized.append(len(chars))  # Length byte

        for b in chars:
            self.tokenized.append(b)

        return True

    # <NUMCON> = <BCD><6-byte-float>
    # ABML: JS(NUMCON) RTN
    ### s_NUMCON = (kBCD, <6-byte-float>, kRTN)

    def f_NUMCON(self):
        """Match a numeric constant and emit cBCD + 6-byte BCD float."""
        # Save position for backtracking
        save_index = self.index

        # Try to parse a number using get_number from ataribasic
        from ataribasic import get_number, encode_bcd
        number, new_index = get_number(self.lbuff, self.index)

        if number is None:
            return False

        # Update parser position to after the number
        self.index = new_index

        # Convert float to 6-byte BCD format
        bcd_bytes = encode_bcd(number)

        # Emit cBCD token (14) + 6 bytes of BCD data
        self.tokenized.append(cBCD)
        for b in bcd_bytes:
            self.tokenized.append(b)

        return True

    # ========================================
        # Emit cSTRVAR token (136-143 for string variables A$-Z$)
        self.tokenized.append(cSTRVAR + vnt_index)

        return True

    # ========================================
    # <EXP> = (<EXP>)<NOP> | <UNARY><EXP> | <NV><NOP> <END>
    # <EXP> = (<EXP>)<NOP> | <UNARY><EXP> | <NV><NOP> <END>
    # ABML: cLPRN CHNG cALPRN JS(EXP) cRPRN JS(NOP) OR JS(UNARY) JS(EXP) OR JS(NV) JS(NOP) RTN
    ### s_EXP = (   cLPRN, kCHNG, cALPRN, s_EXP, cRPRN, s_NOP,  # ( Expr ), Next Op (or end)
    ###     kOR,    s_UNARY, s_EXP,                             # Unary, Expr
    ###     kOR,    s_NV, s_NOP,                                # Numeric value, Next Op (or end)
    ###     kRTN
    ### )

    def f_EXP(self):
        # The assembly's _VEXP / _EXP skips leading blanks before scanning
        # the operand, so a statement like "XIO #1,..." (space after the
        # command) reaches _VEXP at that blank and must advance past it.
        self.util_SKIPBLANK()
        if DEBUG_CODE: print(f"DEBUG f_EXP: index={self.index}, lbuff[{self.index}]={chr(self.lbuff[self.index]) if self.index < len(self.lbuff) else 'EOF'}")

        # Try parenthesized expressions: ( EXP ) NOP
        # ABML Rev.1: CLPRN JS(_EXP) CRPRN JS(_NOP)
        if self.attempt(
            lambda: self.f_CHNG(cLPRN, cLPRN),
            self.f_EXP,
            lambda: self.f_SRCONT(cRPRN),
            self.f_NOP
        ):
            if DEBUG_CODE: print(f"DEBUG f_EXP: matched parenthesized expr, index={self.index}")
            return True

        # Try unary operators: +EXP, -EXP, NOT EXP
        # Use the new f_UNARY implementation that directly checks characters
        if self.f_UNARY():
            if DEBUG_CODE: print(f"DEBUG f_EXP: matched unary, index={self.index}")
            return True

        # Try numeric constants — after matching, try NOP for binary operators
        if self.f_NUMCON():
            if DEBUG_CODE: print(f"DEBUG f_EXP: matched numeric constant, index={self.index}")
            self.f_NOP()   # consume optional operator+rhs (NOP returns True on nada)
            return True

        # Try numeric variables (NV) - NV calls f_TNVAR + f_NMAT, but NOT f_NOP
        # According to ABML: <NV> = <TNVAR> <NMAT> | <TSCON> <NOP>
        # We need to try NV first, then if that fails, try NOP (binary operators)
        if self.attempt(
            self.f_NV,
            self.f_NOP
        ):
            if DEBUG_CODE: print(f"DEBUG f_EXP: matched NV or NOP, index={self.index}")
            return True

        # String expression alternative (e.g. A$ <= B$, CHR$(X)): try STR or STCOMP
        if self.attempt(
            self.f_STR,
            self.f_NOP
        ) or self.f_STCOMP():
            if DEBUG_CODE: print(f"DEBUG f_EXP: matched string expr, index={self.index}")
            return True

        if DEBUG_CODE: print(f"DEBUG f_EXP: no match, index={self.index}")
        return False

    # <NMAT2> = ,<EXP><NOP> | <NADA>
    # ABML: cCOM JS(EXP) JS(NOP) OR RTN
    ### s_NMAT2 = (   cCOM, s_EXP, s_NOP,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_NMAT2(self):
        return (self.attempt(
            lambda: self.f_CHNG(cCOM, cACOM),
            self.f_EXP,
            self.f_NOP
        ) or
        self.f_NADA())

    # <UNARY> = +<EXP> | -<EXP> | NOT<EXP>
    # ABML: cUPLUS CHNG cALPRN JS(EXP) OR cUMINUS CHNG cALPRN JS(EXP) OR cNOT CHNG cALPRN JS(EXP)
    ### s_UNARY = (   cUPLUS, kCHNG, cALPRN, s_EXP,
    ###     kOR,    cUMINUS, kCHNG, cALPRN, s_EXP,
    ###     kOR,    cNOT, kCHNG, cALPRN, s_EXP,
    ###     kRTN
    ### )

    def f_UNARY(self):
        # Unary operators: +EXP, -EXP, NOT EXP
        # Note: Cannot use f_SRCONT because '+' and '-' appear at multiple indices in ops_and_funcs
        # cPLUS=0x25 (37) and cUPLUS=0x35 (53) both map to '+', so search finds the wrong one
        # Must directly check character and emit correct token

        if self.index < len(self.lbuff):
            current_char = chr(self.lbuff[self.index]).upper()

            # Check for + (POSITIVE) - token 0x35
            if current_char == '+':
                self.index += 1
                self.tokenized.append(cUPLUS)
                return self.f_EXP()

            # Check for - (NEGATIVE) - token 0x36
            if current_char == '-':
                self.index += 1
                self.tokenized.append(cUMINUS)
                return self.f_EXP()

            # Check for NOT - token 0x28
            if current_char == 'N' and self.index + 2 < len(self.lbuff):
                next_chars = chr(self.lbuff[self.index+1]).upper() + chr(self.lbuff[self.index+2]).upper()
                if next_chars == 'OT':
                    self.index += 3
                    self.tokenized.append(cNOT)
                    return self.f_EXP()

        return False

    # <NV> = <TNVAR><NOP> | <TSCON><NOP>
    # ABML: TNVAR JS(NOP) OR TSCON JS(NOP) RTN
    ### s_NV = (   s_TNVAR, s_NOP,
    ###     kOR,    s_TSCON, s_NOP,
    ###     kRTN
    ### )

    # <NFUN> = <CNFNP><NFP> | <NFSP><SFP> | <NFUSR>
    # ABML: CNFNP,JS(_NFP),_OR,ANTV(_NFSP),JS(_SFP),_OR,JS(_NFUSR),_RTN
    # ROM: _NFUN at ataribas.asm:1875
    #
    # This rule was MISSING entirely, which is why `PRINT ABS(-5)` tokenized
    # ABS as an undeclared VARIABLE: f_NV went straight to f_TNVAR, which
    # happily consumed the name.
    def f_NFUN(self):
        # CNFNP + (EXP)  -- the numeric functions: ABS, INT, SGN, SQR, RND, ...
        if self.attempt(lambda: self.f_SRCONT(cNFNP), self.f_NFP):
            return True
        # NFSP + (STR)   -- numeric functions taking a string: ASC, LEN, VAL, ADR
        if self.attempt(self.f_NFSP, self.f_SFP):
            return True
        # NFUSR -- USR(expr, expr, ...) with unlimited arguments.
        # ROM _NFUN (ataribas.asm:1875) has THREE alternatives; this third one
        # was missing, so `?USR(A)` fell through to the variable path and
        # registered a bogus VNT entry named `USR(`.
        #   _NFUSR: CUSR, CLPRN,_CHNG,CFLPRN, ANTV(_PUSR), CRPRN, _RTN
        if self.attempt(self.f_NFUSR):
            return True
        return False

    # <NFUSR> = USR( <PUSR> )#
    # ABML: CUSR, CLPRN, _CHNG, CFLPRN, ANTV(_PUSR), CRPRN, _RTN
    def f_NFUSR(self):
        if not self.f_SRCONT(cUSR):
            return False
        # '(' becomes cFLPRN (function left paren), not an array subscript.
        if not self.f_CHNG(cLPRN, cFLPRN):
            return False
        # Arguments are optional -- ANTV retries and falls through on failure.
        self.attempt(self.f_PUSR)
        return self.f_SRCONT(cRPRN)

    def f_NV(self):
        # ABML _NV (ataribas.asm:1835):
        #   JS(_NFUN), _OR, JS(_NVAR), _OR, ESRT(_TNCON), _OR, ANTV(_STCOMP)
        #
        # _NFUN MUST be tried FIRST. Previously f_TNVAR ran first and matched
        # function names like ABS/INT/SGN as variables, emitting 0x80+ and an
        # array-subscript paren instead of the function token + cFLPRN.
        if self.f_NFUN():
            self.f_NOP()
            return True

        if self.f_TNVAR():
            self.f_NMAT()  # consume (EXP) subscript if present, else NADA
            self.f_NOP()   # then optional operator+rhs
            return True

        # STCOMP must be tried BEFORE the bare string constant. Both start by
        # matching a string, so `f_TSCON() or f_STCOMP()` short-circuits on the
        # left operand of `"ABC" < "DEF"` and the comparison is never seen --
        # the operator was dropped from the output entirely.
        #
        # ROM _NV ends `ESRT(_TNCON), _OR, ANTV(_STCOMP)`, where ANTV retries
        # the alternative from the saved position. attempt() is our equivalent,
        # so order the longer production first and let it roll back on failure.
        if self.attempt(self.f_STCOMP):
            self.f_NOP()
            return True

        if self.f_TSCON():
            self.f_NOP()
            return True

        return False

    # <TNCON> = <TSVAR><SMAT> | <TSCON>
    # ABML: TSVAR JS(SMAT) OR ESRT.AD(TSCON) RTN
    ### s_TNCON = (   s_TSVAR, s_SMAT,
    ###     kOR,    s_TSCON,
    ###     kRTN
    ### )

    # NOTE: The correct f_TNVAR implementation is at line 354.
    # This duplicate definition below has been removed as it was broken.

    # Numeric Variable - simplified: just TNVAR + NMAT
    # <NVAR> = <TNVAR> <NMAT> <END>
    # ABML: ESRT.AD(TNVAR) JS(NMAT) RTN
    ### s_NVAR = (f_TNVAR, s_NMAT, kRTN)

    def f_NVAR(self):
        return self.f_TNVAR() and self.f_NMAT()

    # <NOP> = <CHNG><END> | <NADA>
    # ABML: CHNG cACOM OR RTN
    ### s_NOP = (   kCHNG, cACOM,
    ###     kOR,    kNADA,
    ###     kRTN
    ### )

    def f_NOP(self):
        if DEBUG_CODE: print(f"DEBUG f_NOP: index={self.index}, lbuff[{self.index}]={chr(self.lbuff[self.index]) if self.index < len(self.lbuff) else 'EOF'}")
        # Handle binary arithmetic operators: +, -, *, /
        # After matching a numeric value (NV), check for operator followed by another expression
        if self.attempt(
            lambda: self.f_SRCONT(cPLUS),  # + operator - use SRCONT to match "+" and emit cPLUS (0x25)
            self.f_EXP
        ):
            if DEBUG_CODE: print(f"DEBUG f_NOP: matched PLUS, index={self.index}")
            return True

        if self.attempt(
            lambda: self.f_SRCONT(cMINUS),  # - operator
            self.f_EXP
        ):
            if DEBUG_CODE: print(f"DEBUG f_NOP: matched MINUS, index={self.index}")
            return True

        if self.attempt(
            lambda: self.f_SRCONT(cMUL),  # * operator
            self.f_EXP
        ):
            if DEBUG_CODE: print(f"DEBUG f_NOP: matched MUL, index={self.index}")
            return True

        if self.attempt(
            lambda: self.f_SRCONT(cDIV),  # / operator
            self.f_EXP
        ):
            if DEBUG_CODE: print(f"DEBUG f_NOP: matched DIV, index={self.index}")
            return True

        # Comparison operators: <=, <>, >=, <, > (tokens 29-33)
        if self.attempt(
            lambda: self.f_SRCONT(29),  # <= operator
            self.f_EXP
        ):
            if DEBUG_CODE: print(f"DEBUG f_NOP: matched LTE, index={self.index}")
            return True

        if self.attempt(
            lambda: self.f_SRCONT(30),  # <> operator
            self.f_EXP
        ):
            if DEBUG_CODE: print(f"DEBUG f_NOP: matched NEQ, index={self.index}")
            return True

        if self.attempt(
            lambda: self.f_SRCONT(31),  # >= operator
            self.f_EXP
        ):
            if DEBUG_CODE: print(f"DEBUG f_NOP: matched GTE, index={self.index}")
            return True

        if self.attempt(
            lambda: self.f_SRCONT(32),  # < operator
            self.f_EXP
        ):
            if DEBUG_CODE: print(f"DEBUG f_NOP: matched LT, index={self.index}")
            return True

        if self.attempt(
            lambda: self.f_SRCONT(33),  # > operator
            self.f_EXP
        ):
            if DEBUG_CODE: print(f"DEBUG f_NOP: matched GT, index={self.index}")
            return True

        # EQUALS operator (=) - token 34 (0x22)
        if self.attempt(
            lambda: self.f_SRCONT(34),  # = operator (cEQ)
            self.f_EXP
        ):
            if DEBUG_CODE: print(f"DEBUG f_NOP: matched EQ, index={self.index}")
            return True

        # AND operator - token 42 (0x2A)
        if self.attempt(
            lambda: self.f_SRCONT(42),  # AND operator
            self.f_EXP
        ):
            if DEBUG_CODE: print(f"DEBUG f_NOP: matched AND, index={self.index}")
            return True

        # OR operator - token 41 (0x29)
        if self.attempt(
            lambda: self.f_SRCONT(41),  # OR operator
            self.f_EXP
        ):
            if DEBUG_CODE: print(f"DEBUG f_NOP: matched OR, index={self.index}")
            return True

        # Exponentiation operator (^) - token 0x23 (CEXP), Rev.2+ only
        if self.attempt(
            lambda: self.f_SRCONT(35),  # ^ operator (cCEXP = 0x23)
            self.f_EXP
        ):
            if DEBUG_CODE: print(f"DEBUG f_NOP: matched EXP (^), index={self.index}")
            return True

        # Nothing matched - allow NADA (nothing)
        if DEBUG_CODE: print(f"DEBUG f_NOP: nada, index={self.index}")
        return self.f_NADA()

    # Parentheses wrapping One or more comma-delimited Mathematical expressions
    # <NMAT> = ( <EXP> <NMAT2> ) | <NADA> <END>
    # ABML: cLPRN CHNG cALPRN VEXP JS(NMAT2) cRPRN OR RTN
    ### s_NMAT = (  cLPRN, kCHNG, cALPRN, s_EXP, s_NMAT2, cRPRN,  # ( Expr [, Expr ...] )
    ###     kOR,    # nada #                                      # or Nothing
    ###     kRTN
    ### )

    def f_NMAT(self):
        return (self.attempt(
            lambda: self.f_CHNG(cLPRN, cALPRN),
            self.f_EXP,
            self.f_NMAT2,
            lambda: self.f_SRCONT(cRPRN)
        ) or
        self.f_NADA())

    # Continuation of comma-delimited Mathematical expressions
    # <NMAT2> = , <EXP> | <NADA> <END>
    # ABML: cCOM CHNG cACOM VEXP OR RTN
    ### s_NMAT2 = ( cCOM, kCHNG, cACOM, s_EXP,      # Array Comma, Expr
    ###     kOR,    # nada #                        # or Nothing
    ###     kRTN
    ### )

    def f_NMAT2(self):
        return (self.attempt(
            lambda: self.f_CHNG(cCOM, cACOM),
            self.f_EXP
        ) or
        self.f_NADA())

    # <SMAT> = (<EXP><SMAT2>) | <NADA> <END>
    # ABML: cLPRN CHNG cSLPRN VEXP JS(SMAT2) cRPRN OR RTN
    ### s_SMAT = (  cLPRN, kCHNG, cSLPRN, s_EXP, s_SMAT2, cRPRN,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_SMAT(self):
        # cSLPRN (0x37), NOT cALPRN (0x38). ataribas.asm:1923 _SMAT uses
        # CSLPRN where _NMAT (:1863) uses CALPRN -- a string subscript and a
        # numeric array subscript are different tokens. This rule was copied
        # from the numeric twin and kept its paren, so every A$(...) substring
        # was off by one byte.
        return (self.attempt(
            lambda: self.f_CHNG(cLPRN, cSLPRN),
            self.f_EXP,
            self.f_SMAT2,
            lambda: self.f_SRCONT(cRPRN)
        ) or
        self.f_NADA())

    # <SMAT2> = ,<EXP> | <NADA> <END>
    # ABML: cCOM CHNG cACOM VEXP OR RTN
    ### s_SMAT2 = ( cCOM, kCHNG, cACOM, s_EXP,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_SMAT2(self):
        return (self.attempt(
            lambda: self.f_CHNG(cCOM, cACOM),
            self.f_EXP
        ) or
        self.f_NADA())

    # <STCOMP> = <STR><SOP><STR> <END>
    # ABML: JS(STR) JS(SOP) JS(STR) RTN
    ### s_STCOMP = (s_STR, s_SOP, s_STR, kRTN)

    def f_STCOMP(self):
        return self.attempt(
            self.f_STR,
            self.f_SOP,
            self.f_STR
        )

    # <STR> = <SFUN> | <SVAR> | <SCON> <END>
    # ABML: JS(SFUN) OR JS(SVAR) OR ESRT.AD(TSCON) RTN
    ### s_STR = (   s_SFUN,
    ###     kOR,    s_SVAR,
    ###     kOR,    s_TSCON,
    ###     kRTN
    ### )

    def f_STR(self):
        # Atari BASIC treats blanks as insignificant between tokens, so skip any
        # leading blanks before trying the string alternatives. Without this,
        # `LOAD "S:TEST.BAS"` (space before the quote) fails while
        # `LOAD"S:TEST.BAS"` (no space) succeeds — both are valid in real BASIC.
        self.util_SKIPBLANK()
        return (self.attempt(
            self.f_SFUN
        ) or
        self.attempt(
            self.f_SVAR
        ) or
        self.attempt(
            self.f_TSCON
        ))

    # <SFUN> = SFNP <NFP> <END>
    # ABML: ANTV.AD(SFNP) JS(NFP) RTN
    ### s_SFUN = (s_SFNP, s_NFP, kRTN)

    def f_SFUN(self):
        return self.attempt(
            self.f_SFNP,
            self.f_NFP
        )

    # <SVAR> = <TSVAR> <SMAT> <END>
    # ABML: ESRT.AD(TSVAR) JS(SMAT) RTN
    ### s_SVAR = (f_TSVAR, s_SMAT, kRTN)

    def f_SVAR(self):
        return self.attempt(
            self.f_TSVAR,
            self.f_SMAT
        )

    # <SFP> = <STR>) <END>
    # ABML: cLPRN CHNG cFLPRN JS(STR) cRPRN RTN
    ### s_SFP = (cLPRN, kCHNG, cFLPRN, s_STR, cRPRN, kRTN)

    def f_SFP(self):
        return self.attempt(
            lambda: self.f_CHNG(cLPRN, cFLPRN),
            self.f_STR,
            lambda: self.f_SRCONT(cRPRN)
        )

    # <SOP> = <= cLE :CHNG cSLE <OR>
    #   <> cNE :CHNG cSNE <OR>
    #   <  cLT :CHNG cSLT <OR>
    #   >  cGT :CHNG cSGT <OR>
    # ABML: cLE CHNG cSLE OR cNE CHNG cSNE OR cLT CHNG cSLT OR cGT CHNG cSGT RTN
    ### s_SOP = (   cLE, kCHNG, cSLE,
    ###     kOR,    cNE, kCHNG, cSNE,
    ###     kOR,    cLT, kCHNG, cSLT,
    ###     kOR,    cGT, kCHNG, cSGT,
    ###     kRTN
    ### )

    def f_SOP(self):
        # Six string comparison operators, in the ROM's exact order.
        # _SOP (ataribas.asm):
        #     CLE,_CHNG,CSLE / CNE,_CHNG,CSNE / CGE,_CHNG,CSGE
        #     CGT,_CHNG,CSGT / CLT,_CHNG,CSLT / CEQ,_CHNG,CSEQ
        #
        # The pairing is positional, so the string token is always the numeric
        # token + 0x12:  0x1D->0x2F  0x1E->0x30  0x1F->0x31
        #                0x20->0x32  0x21->0x33  0x22->0x34
        #
        # BEWARE THE NAMES. ataridefs.py mirrors the ROM's own swapped labels:
        # cGT is 0x20 but means '<', and cLT is 0x21 but means '>' (the ROM
        # comments at ataribas.asm:3610 say the same). The cS* names are
        # swapped to match. Pair by VALUE, not by name, or '<' and '>' come
        # out transposed. Hence cGT->cSLT and cLT->cSGT below: both are
        # +0x12 and both are correct.
        #
        # ORDER MATTERS: two-character operators must be tried before their
        # one-character prefixes, or '>=' matches as '>' and leaves '='.
        #
        # cSGE (>=) and cSEQ (=) were previously MISSING here entirely.
        return (self.attempt(
            lambda: self.f_CHNG(cLE, cSLE)      # 0x1D -> 0x2F   <=
        ) or
        self.attempt(
            lambda: self.f_CHNG(cNE, cSNE)      # 0x1E -> 0x30   <>
        ) or
        self.attempt(
            lambda: self.f_CHNG(cGE, cSGE)      # 0x1F -> 0x31   >=
        ) or
        self.attempt(
            lambda: self.f_CHNG(cGT, cSLT)      # 0x20 -> 0x32   <
        ) or
        self.attempt(
            lambda: self.f_CHNG(cLT, cSGT)      # 0x21 -> 0x33   >
        ) or
        self.attempt(
            lambda: self.f_CHNG(cEQ, cSEQ)      # 0x22 -> 0x34   =
        ))

    # <SFNP> = SFN <END>
    # ABML: ESRT.AD(SFN) RTN
    ### s_SFNP = (s_SFN, kRTN)

    def f_SFNP(self):
        # SFN matches ANY function name in the operator/function table.
        # Assembly: ESRT.AD(SFN) looks up the name in ops_and_funcs[0x3D..0x50].
        #
        # The list below MUST cover every function token. ABS (0x4F) and INT
        # (0x50) were missing, so `ABS(-5)` matched no function and fell
        # through to the variable path, tokenizing as an undeclared VARIABLE
        # (0x80+) with an array-subscript paren (cALPRN 0x38) instead of
        # cABS + cFLPRN (0x4F 0x3A).
        #
        # NOTE: cEXP is defined TWICE in ataridefs.py -- 0x23 (the '^'
        # operator) and 0x4A (the EXP() function). The later definition wins,
        # so cEXP here is the function; cFEXP names it explicitly to make that
        # intent obvious and survive any reordering of the constants.
        cFEXP = 0x4A            # EXP() function, NOT '^' (0x23)
        return (self.f_SRCONT(cSTR) or self.f_SRCONT(cCHR) or self.f_NFSP()
                or self.f_SRCONT(cUSR) or self.f_SRCONT(cADR) or self.f_SRCONT(cATN)
                or self.f_SRCONT(cCOS) or self.f_SRCONT(cPEEK) or self.f_SRCONT(cSIN)
                or self.f_SRCONT(cRND) or self.f_SRCONT(cFRE) or self.f_SRCONT(cFEXP)
                or self.f_SRCONT(cLOG) or self.f_SRCONT(cCLOG) or self.f_SRCONT(cSQR)
                or self.f_SRCONT(cSGN) or self.f_SRCONT(cABS) or self.f_SRCONT(cINT))

    # <NFP> = (<EXP>) | <END>
    # ABML: cLPRN CHNG cFLPRN JS(EXP) cRPRN OR RTN
    ### s_NFP = (   cLPRN, kCHNG, cFLPRN, s_EXP, cRPRN,
    ###     kOR,    kRTN
    ### )

    def f_NFP(self):
        return (self.attempt(
            lambda: self.f_CHNG(cLPRN, cFLPRN),
            self.f_EXP,
            lambda: self.f_SRCONT(cRPRN)
        ) or
        self.f_NADA())

    # String operator - Convert compare opss to string compares
    # <SOP> =
    #   <= cLE :CHNG cSLE <OR>
    #   <> cNE :CHNG cSNE <OR>
    #   <  cLT :CHNG cSLT <OR>
    #   >  cGT :CHNG cSGT <OR>
    #   <  cGT :CHNG cSGT <OR>
    #   <  cEQ :CHNG cSEQ <END>
    # ABML: cLE CHNG cSLE OR cNE CHNG cSNE OR cLT CHNG cSLT OR cGT CHNG cSGT OR cEQ CHNG cSEQ RTN
    ### s_SOP = (   cLE, kCHNG, cSLE,
    ###     kOR,    cNE, kCHNG, cSNE,
    ###     kOR,    cLT, kCHNG, cSLT,
    ###     kOR,    cGT, kCHNG, cSGT,
    ###     kOR,    cEQ, kCHNG, cSEQ,
    ###     kRTN
    ### )

    # ========================================

    # PUT Command with Device #, Comma, Numeric Parameter
    # <PUT> = <D1> , <EXP> <EOS> <END>
    # ABML: cPND VEXP cCOM ... VEXP ... JS(EOS) RTN
    ### s_PUT = (cPND, s_EXP, cCOM, s_EXP, s_EOS, kRTN)

    def f_PUT(self):
        result = self.f_SRCONT(cPND) and self.f_EXP() and self.f_SRCONT(cCOM) and self.f_EXP() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # Statements with one Numeric Parameter
    # <GOTO> = <EXP> <EOS> <END>
    # ABML: VEXP ... JS(EOS) RTN
    ### s_GR = (s_EXP, s_EOS, kRTN)
    ### s_TRAP = s_GOTO = s_GOSUB = s_COLOR = s_GR

    def f_GR(self):
        result = self.f_EXP() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result
    f_TRAP = f_GOTO = f_GOSUB = f_COLOR = f_GR

    # Statements with no parameters
    # <DOS> = <EOS> <END>
    # ABML: JS(EOS) RTN
    ### s_RAD = (s_EOS, kRTN)

    def f_RAD(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    # <CSAVE> = <EOS> | <END> - Save cassette
    def f_CSAVE(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    # <CLOAD> = <EOS> | <END> - Load cassette
    def f_CLOAD(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    # <DOS> = <EOS> | <END> - DOS command
    def f_DOS(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    # <RET> = <EOS> | <END> - Return (alias for RETURN)
    def f_RET(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    # <CLR> = <EOS> | <END> - Clear screen/memory (token 18)
    def f_CLR(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    # <DEG> = <EOS> | <END> - Set angle mode to degrees (token 19)
    def f_DEG(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    # <END> = <EOS> | <END> - End program (token 21)
    def f_END(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    # <NEW> = <EOS> | <END> - Clear program (token 22)
    def f_NEW(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    # <STOP> = <EOS> | <END> - Stop program execution (token 38)
    def f_STOP(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    # <POP> = <EOS> | <END> - Pop from stack (token 39)
    def f_POP(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    f_BYE = f_CONT = f_RAD  # BYE and CONT still use RAD

    # ========================================

    # LET and Implied LET. Numeric or String.
    # <LET> = <NVAR> = <EXP> <EOS> | <SVAR> = <STR> <EOS> <END>
    # ABML: ANTV.AD(NVAR) cEQ CHNG cAASN VEXP JS(EOS) OR ANTV.AD(SVAR) cEQ CHNG cSASN ANTV.AD(STR) JS(EOS) RTN
    ### s_LET = (   s_NVAR, cEQ, kCHNG, cAASN, s_EXP, s_EOS,
    ###     kOR,    s_SVAR, cEQ, kCHNG, cSASN, s_STR, s_EOS,
    ###     kRTN
    ### )
    ### s_ILET = s_LET

    def f_LET(self):
        # Use attempt() for proper backtracking between numeric and string assignment branches
        # ABML: NVAR cEQ CHNG cAASN EXP EOS OR SVAR cEQ CHNG cSASN STR EOS RTN

        # Try numeric assignment first: VAR = EXP <EOS>
        # ROM _SLET (ataribas.asm:1987): ANTV(_NVAR),CEQ,_CHNG,CAASN,_VEXP,JS(_EOS)
        # CEQ matches the '=' and emits cEQ; _CHNG then OVERWRITES that byte with
        # CAASN (ECHNG: outbuff[cox-1] = next code byte). So a numeric assignment
        # is stored as 0x2D (cAASN), NOT 0x22 (cEQ, which is also the comma).
        # Passing (cEQ, cEQ) made CHNG a no-op and left every `A=5` with 0x22.
        if DEBUG_CODE: print(f"DEBUG f_LET: starting, index={self.index}")
        result1 = self.attempt(self.f_NVAR, lambda: self.f_CHNG(cEQ, cAASN), lambda: self.f_EXP(), lambda: self.f_EOS())
        if DEBUG_CODE: print(f"DEBUG f_LET: result1={result1}, index after numeric attempt={self.index}")

        # Only try string assignment if numeric failed
        result2 = False
        if not result1:
            if DEBUG_CODE: print(f"DEBUG f_LET: trying string assignment")
            result2 = self.attempt(self.f_SVAR, lambda: self.f_CHNG(cEQ, cSASN), lambda: self.f_STR(), lambda: self.f_EOS())
            if DEBUG_CODE: print(f"DEBUG f_LET: result2={result2}, index after string attempt={self.index}")

        result = result1 or result2
        if DEBUG_CODE: print(f"DEBUG f_LET: final result={result}")

        if result:
            # Append end-of-line marker (cCR = 22)
            self.f_append_cCR()
        return result
    f_ILET = f_LET

    # FOR Statement
    # <FOR> = <TNVAR> = <EXP> TO <EXP> <FSTEP> <EOS> <END>
    # ABML: ESRT.AD(TNVAR) cEQ CHNG cAASN VEXP cTO VEXP JS(FSTEP) JS(EOS) RTN
    ### s_FOR = (f_TNVAR, cEQ, kCHNG, cAASN, s_EXP, cTO, s_EXP, s_FSTEP, s_EOS, kRTN)

    def f_FOR(self):
        result = (self.f_TNVAR() and self.f_CHNG(cEQ, cAASN) and self.f_EXP()
            and self.f_SRCONT(cTO) and self.f_EXP() and self.f_FSTEP() and self.f_EOS())
        if result:
            self.f_append_cCR()
        return result

    # STEP with Numeric Expression at the end of a FOR Statement
    # <FSTEP> = STEP <EXP> | <NADA>
    # ABML: cSTEP VEXP OR RTN
    ### s_FSTEP = ( cSTEP, s_EXP,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_FSTEP(self):
        return ((self.f_SRCONT(cSTEP) and self.f_EXP())
            or  self.f_NADA()    )

    # LOCATE Statement
    # <LOCATE> = <EXP> , <EXP> , <TNVAR> <EOL> <END>
    # ABML: VEXP cCOM VEXP cCOM JS(NEXT) RTN
    ### s_LOCATE = (s_EXP, cCOM, s_EXP, cCOM, s_NEXT, kRTN)

    def f_LOCATE(self):
        result = self.f_EXP() and self.f_SRCONT(cCOM) and self.f_EXP() and self.f_SRCONT(cCOM) and self.f_NEXT()
        if result:
            # f_NEXT handles cCR internally, nothing more to do
            pass
        return result

    # GET Statement - Requires Device #, comma, Variable Name
    # <GET> = <D1> , <TNVAR> <END>
    # ABML: JS(D1) cCOM ... ESRT.AD(TNVAR) JS(EOS) RTN
    ### s_GET = (s_D1, cCOM, self.f_TNVAR, s_EOS, kRTN)

    def f_GET(self):
        result = self.f_D1() and self.f_SRCONT(cCOM) and self.f_TNVAR() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # NEXT Statement - Requires Variable Name
    # <NEXT> = <TNVAR> <EOS> <END>
    # ABML: ESRT.AD(TNVAR) JS(EOS) RTN
    ### s_NEXT = (f_TNVAR, s_EOS, kRTN)

    def f_NEXT(self):
        result = self.f_TNVAR() and self.f_EOS()
        if result:
            self.f_append_cCR()
        else:
            self.f_append_cCR()
        return True

    # ========================================
    # GOSUB Statement - Call subroutine at line number or expression
    # <GOSUB> = <EXP> <EOS> | <EOS> <END>
    # ABML: VEXP JS(EOS) OR JS(EOS) RTN
    ### s_GOSUB = (   s_EXP, s_EOS,
    ###     kOR,        s_EOS,
    ###     kRTN
    ### )

    def f_GOSUB(self):
        if self.f_EXP() and self.f_EOS():
            return self.f_append_cCR()
        return self.f_append_cCR()

    # ========================================
    # RETURN Statement - Return from subroutine
    # <RETURN> = <EOS> | <END>
    ### s_RETURN = (s_EOS, kRTN)

    def f_RETURN(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    # ========================================
    # GOTO Statement - Jump to line number or expression
    # <GOTO> = <EXP> <EOS> | <END>
    # ABML: VEXP JS(EOS) OR RTN
    ### s_GOTO = (   s_EXP, s_EOS,
    ###     kOR,        # nada #
    ###     kRTN
    ### )

    def f_GOTO(self):
        if self.f_EXP() and self.f_EOS():
            return self.f_append_cCR()
        return self.f_append_cCR()

    # ========================================
    # GO TO Statement - Same as GOTO (alternative spelling)
    # <GO TO> = <EXP> <EOS> | <END>
    # ABML: VEXP JS(EOS) OR RTN
    ### s_GO_TO = (   s_EXP, s_EOS,
    ###     kOR,        # nada #
    ###     kRTN
    ### )

    def f_GO_TO(self):
        result = self.f_EXP() or True
        if result and self.f_EOS():
            self.f_append_cCR()
        return result

    # ========================================
    # TRAP Statement - Set error trap to line number or expression
    # <TRAP> = <EXP> | <END>
    # ABML: VEXP OR RTN
    ### s_TRAP = (   s_EXP,
    ###     kOR,        # nada #
    ###     kRTN
    ### )

    def f_TRAP(self):
        if self.f_EXP() and self.f_EOS():
            return self.f_append_cCR()
        return self.f_append_cCR()

    # ========================================
    # BYE Statement - End program execution
    # <BYE> = <EOS> | <END>
    # ABML: JS(EOS) OR RTN
    ### s_BYE = (   s_EOS,
    ###     kOR,        # nada #
    ###     kRTN
    ### )

    def f_BYE(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    # ========================================
    # CONT Statement - Continue from STOP/BREAK
    # <CONT> = <EOS> | <END>
    # ABML: JS(EOS) OR RTN
    ### s_CONT = (   s_EOS,
    ###     kOR,        # nada #
    ###     kRTN
    ### )

    def f_CONT(self):
        self.f_EOS()  # consume : or end-of-input
        self.f_append_cCR()
        return True

    # ========================================
    # RESTORE Statement with Optional Line Number
    # <RESTORE> = <EXP> <EOS> | <EOS> <END>
    # ABML: VEXP JS(EOS) OR JS(EOS) RTN
    ### s_RESTORE = (   s_EXP, s_EOS,
    ###     kOR,        s_EOS,
    ###     kRTN
    ### )

    def f_RESTORE(self):
        result = (self.f_EXP() or self.f_NADA()) and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # ========================================
    # INPUT Statement with Optional Prompt and One or More Variables
    # <INPUT> = <OPD> <READ> <END>
    # ABML: JS(OPD) JS(NSVRL) JS(EOS) RTN
    ### s_INPUT = (s_OPD, s_NSVRL, s_EOS, kRTN)

    def f_INPUT(self):
        result = self.f_OPD() and self.f_NSVRL() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # ========================================
    # READ Statement with One or More Variables
    # <READ> = <NSVARL> <EOS> <END>
    # ABML: JS(NSVRL) JS(EOS) RTN
    ### s_READ = (s_NSVRL, s_EOS, kRTN)

    def f_READ(self):
        result = self.f_NSVRL() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # ========================================
    # End of Statement or End of Line
    # <EOS> = : | CR <END>
    # ABML: cEOS OR cCR RTN
    ### s_EOS = (   cEOS,
    ###     kOR,    cCR,
    ###     kRTN
    ### )
    ### s_EOS2 = s_EOS # Identical to EOS

    def f_EOS(self):
        # Check for colon (cEOS = 14) or cCR (22), OR end of input
        # Also accept ATASCII newline (0x9B) as valid terminator
        if self.index < len(self.lbuff):
            return (self.f_SRCONT(cEOS)
                or  self.f_SRCONT(cCR)
                or  (self.index == len(self.lbuff) - 1 and self.lbuff[self.index] == 0x9B))
        else:
            # End of input is also valid EOS
            return True
    f_EOS2 = f_EOS

    # ========================================
    # PRINT Statement with optional Device # and Delimited Values
    # <PRINT> = <D1> <EOS> | <D1> <PR1> <EOS> <END>
    # ABML: JS(D1) JS(EOS) OR JS(OPD) ANTV.AD(PR1) JS(EOS) RTN
    ### s_PRINT = ( s_D1, s_EOS,
    ###     kOR,    s_D1, s_PR1, s_EOS,
    ###     kRTN
    def f_PRINT(self):
        result = (self.attempt(self.f_D1, self.f_PR1)
                  or self.attempt(self.f_D1)
                  or self.attempt(self.f_PR1))
        if result and self.f_EOS():
            self.f_append_cCR()
        return result

    # ? (PRINT alias) - Same as PRINT but uses token 40
    def f_QUESTION(self): return self.f_PRINT()

    # LPRINT Statement with optional Delimited Values
    # <LPRINT> = <PR1> <EOS> <END>
    # ABML: ANTV.AD(PR1) JS(EOS) RTN
    ### s_LPRINT = (s_PR1, s_EOS, kRTN)

    def f_LPRINT(self):
        result = (self.f_PR1() or self.f_NADA()) and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # ========================================
    # Device ID
    # <D1> = <cPND> <EXP> <END>
    # ABML: cPND VEXP RTN
    ### s_D1 = (cPND, s_EXP, kRTN)

    def f_D1(self):
        return self.f_SRCONT(cPND) and self.f_EXP()

    # ========================================
    # Numeric or String Variable
    # <NSVAR> = <NVAR> | <SVAR> <END>
    # ABML: ESRT.SD(TNVAR) OR ESRT.AD(TSVAR) RTN
    ### s_NSVAR = ( self.f_TNVAR,
    ###     kOR,    self.f_TSVAR,
    ###     kRTN
    ### )

    def f_NSVAR(self): return self.f_TNVAR() or self.f_TSVAR()

    # Numeric and/or String Variable List
    # <NSVRL> = <NSVAR> <NSV2> | <NADA> <END>
    # ABML: JS(NVAR) JS(NSV2) OR RTN
    ### s_NSVRL = ( s_NSVAR, s_NSV2,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_NSVRL(self):
        return ((self.f_NSVAR() and self.f_NSV2())
            or  self.f_NADA()    )

    # Continuation of comma-delimited Numeric and/or String Variables
    # <NSV2> = ,<NSVRL> | <NADA> <END>
    # ABML: cCOM JS(NSVRL) OR RTN
    ### s_NSV2 = (  cCOM, s_NSVRL,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_NSV2(self):
        return ((self.f_SRCONT(cCOM) and self.f_NSVRL())
            or  self.f_NADA()    )

    # ========================================
    # XIO Command Arguments
    # Example: XIO 18,#6,0,0,"S:"
    # <XIO> = <EXP> , <D1> , <EXP> , <EXP> , <FS> <EOS> <END>
    # ABML: VEXP cCOM JS(D1) cCOM JS(TEXP) cCOM JS(FS) JS(EOS) RTN
    ### s_XIO = (s_EXP, cCOM, s_D1, cCOM, s_TEXP, cCOM, s_FS, s_EOS, kRTN)

    def f_XIO(self):
        result = self.f_EXP() and self.f_SRCONT(cCOM) and self.f_D1() and self.f_SRCONT(cCOM) and self.f_TEXP() and self.f_SRCONT(cCOM) and self.f_FS() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # ========================================
    # OPEN Command Arguments
    # Example: OPEN #1,4,0,"K:"
    # <OPEN> = <D1> , <EXP> , <EXP> , <FS> <EOS> <END>
    # ABML: JS(D1) cCOM JS(TEXP) cCOM JS(FS) JS(EOS) RTN
    ### s_OPEN = (s_D1, cCOM, s_TEXP, cCOM, s_FS, s_EOS, kRTN)

    def f_OPEN(self):
        result = self.f_D1() and self.f_SRCONT(cCOM) and self.f_TEXP() and self.f_SRCONT(cCOM) and self.f_FS() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # ========================================
    # CLOSE Command Arguments
    # <CLOSE> = <D1> <EOS> <END>
    # ABML: JS(D1) JS(EOS) RTN
    # Example: CLOSE #1
    ### s_CLOSE = (s_D1, s_EOS, kRTN)

    def f_CLOSE(self):
        result = self.f_D1() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # ========================================
    # ENTER, LOAD, SAVE Command Arguments
    # <LOAD> = <FS><EOS> <END>
    # ABML: JS(FS) JS(EOS) RTN
    ### s_ENTER = (s_FS, s_EOS, kRTN)
    ### s_LOAD = s_SAVE = s_ENTER

    def f_ENTER(self):
        result = self.f_FS() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result
    f_LOAD = f_SAVE = f_ENTER

    # ========================================
    # RUN Command Arguments
    # <RUN> = <FS> <EOS> | <EOS> <END>
    # ABML: JS(FS) JS(EOS) OR JS(EOS) RTN
    ### s_RUN = (   s_FS, s_EOS,
    ###     kOR,    s_EOS,
    ###     kRTN
    ### )

    def f_RUN(self):
        result = (self.f_FS() or self.f_NADA()) and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # ========================================
    # Optional Device ID argument
    # <OPD> = <D1>, | <NADA> <END>
    # ABML: JS(D1) cCOM OR RTN
    ### s_OPD = (   s_D1, cCOM,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_OPD(self):
        return ((self.f_D1() and self.f_SRCONT(cCOM))
            or  self.f_NADA()    )

    # ========================================
    # List Command Arguments
    # <LIST> = <FS> : | <FS> , <LIS> | <LIS> <END>
    # (But original source has "<LIST> = <FS>;<L2> | <L2> <END>")
    # ABML: JS(FS) JS(EOS) OR JS(FS) cCOM JS(LIS) OR JS(LIS) RTN
    # Examples:
    #   LIST "C:PROGRAM.LST"
    #   LIST "C:PROGRAM.LST", 30, 80
    #   LIST 20
    #   LIST
    ### s_LIST = (  s_FS, s_EOS,
    ###     kOR,    s_FS, cCOM, s_LIS,
    ###     kOR,    s_LIS,
    ###     kRTN
    ### )

    def f_LIST(self):
        result = ((self.f_FS() and (self.f_EOS() or self.index >= len(self.lbuff)))
            or  (self.f_FS() and self.f_SRCONT(cCOM) and self.f_LIS())
            or  self.f_LIS()     )
        if result:
            # EOS was consumed or implicit at end of input, append cCR for end of line
            self.f_append_cCR()
        return result

    # Zero, One, or Two Line Numbers and End of Statement
    # <LIS> = <L1> <EOS> <END>
    # ABML: ANTV.AD(L1) JS(EOS) RTN
    ### s_LIS = (s_L1, s_EOS, kRTN)

    def f_LIS(self): return self.f_L1() and (self.f_EOS() or self.index >= len(self.lbuff))

    # ========================================
    # STATUS Command Complete Statement
    # <STATUS> = <STAT> <EOS> <END>
    # ABML: JS(STAT) JS(EOS) RTN
    ### s_STATUS = (s_STAT, s_EOS, kRTN)

    def f_STATUS(self):
        result = self.f_STAT() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # Status command arguments
    # <STAT> = <D1> , <NVAR> <END>
    # ABML: JS(D1) cCOM JS(NVAR) RTN
    ### s_STAT = (s_D1, cCOM, s_NVAR, kRTN)

    def f_STAT(self): return self.f_D1() and self.f_SRCONT(cCOM) and self.f_NVAR()

    # ========================================
    # NOTE, POINT Complete Statement
    # <NOTE> = <STAT> , <NVAR> <EOS> <END>
    # ABML: JS(STAT) cCOM ANTV.AD(NVAR) JS(EOS) RTN
    ### s_NOTE = (s_STAT, cCOM, s_NVAR, s_EOS, kRTN)
    ### s_POINT = s_NOTE

    def f_NOTE(self):
        result = self.f_STAT() and self.f_SRCONT(cCOM) and self.f_NVAR() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result
    f_POINT = f_NOTE

    # ========================================
    # Filestring is just a String
    # <FS> = <STR>
    # ABML: JS(STR) RTN
    ### s_FS = (s_STR, kRTN)

    def f_FS(self): return self.f_STR()

    # ========================================
    # TEXP : Two Expressions Rule
    # <TEXP> = <EXP> , <EXP> <END>
    # ABML: VEXP cCOM VEXP RTN
    ### s_TEXP = (s_EXP, cCOM, s_EXP, kRTN)

    def f_TEXP(self): return self.f_EXP() and self.f_SRCONT(cCOM) and self.f_EXP()

    # ========================================
    # SOUND Command Complete Statement (4 Numeric Expressions)
    # <SOUND> = <EXP> , <EXP> , <EXP> , <EXP> <EOS> <END>
    # ABML: VEXP cCOM VEXP cCOM VEXP cCOM VEXP JS(EOS) RTN
    ### s_SOUND = (s_EXP, cCOM, s_EXP, cCOM, s_EXP, cCOM, s_EXP, s_EOS, kRTN)

    def f_SOUND(self):
        result = self.f_EXP() and self.f_SRCONT(cCOM) and self.f_EXP() and self.f_SRCONT(cCOM) and self.f_EXP() and self.f_SRCONT(cCOM) and self.f_EXP() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # ========================================
    # SETCOLOR Command Complete Statement (3 Numeric Expressions)
    # <SETCOLOR> = <EXP> , <EXP> , <EXP> <EOS> <END>
    # ABML: VEXP cCOM VEXP cCOM VEXP JS(EOS) RTN
    ### s_SETCOLOR = (s_EXP, cCOM, s_EXP, cCOM, s_EXP, s_EOS, kRTN)

    def f_SETCOLOR(self):
        result = self.f_EXP() and self.f_SRCONT(cCOM) and self.f_EXP() and self.f_SRCONT(cCOM) and self.f_EXP() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # ========================================
    # POKE, PLOT, POSITION, DRAWTO Complete Statement (2 Numeric Expressions)
    # <POKE> = <EXP> , <EXP> <EOS> <END>
    # ABML: JS(TEXP) JS(EOS) RTN
    ### s_POKE = (s_TEXP, s_EOS, kRTN)
    ### s_PLOT = s_POS = s_DRAWTO = s_POKE

    def f_POKE(self):
        result = self.f_TEXP() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result
    f_PLOT = f_POS = f_DRAWTO = f_POKE

    # ========================================
    # DIM, COM Complete Statement (Zero or more dimensioned vars)
    # (Yes, DIM and COM can have zero vars.)
    # <DIM> = <NSML> <EOS> <END>
    # ABML: JS(NSML) JS(EOS) RTN
    ### s_DIM = (s_NSML, s_EOS, kRTN)
    ### s_COM = s_DIM

    def f_DIM(self):
        result = self.f_NSML() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result
    f_COM = f_DIM

    # ========================================
    # ON : ON...GOTO/GOSUB Complete Statement
    # <ON> = <EXP> <ON1> <EXPL> <EOS> <END>
    # ABML: VEXP JS(ON1) JS(EXPL) JS(EOS) RTN
    ### s_ON = (s_EXP, s_ON1, s_EXPL, s_EOS, kRTN)

    def f_ON(self):
        result = self.f_EXP() and self.f_ON1() and self.f_EXPL() and self.f_EOS()
        if result:
            self.f_append_cCR()
        return result

    # <ON1> = GOTO | GOSUB <END>
    # ABML: cGTO OR cGS RTN
    ### s_ON1 = (   cGTO,
    ###     kOR,    cGS,
    ###     kRTN
    ### )

    def f_ON1(self): return self.f_SRCONT(cGTO) or self.f_SRCONT(cGS)

    # EXPL : Expression List
    # <EXPL> = <EXP> <EXPL1> <END>
    # ABML: VEXP JS(EXPL1) RTN
    ### s_EXPL = (s_EXP, s_EXPL1, kRTN)

    def f_EXPL(self): return self.f_EXP() and self.f_EXPL1()

    # EXPL1 : Expression List Continuation
    # <EXPL1> = , <EXPL> | <NADA> <END>
    # ABML: cCOM JS(EXPL) OR RTN
    ### s_EXPL1 = ( cCOM, s_EXPL,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_EXPL1(self):
        return ((self.f_SRCONT(cCOM) and self.f_EXPL())
            or  self.f_NADA()    )

    # ========================================
    # EOS : End of Statement or End of Line
    # <EOS> = CEOS | CCR <END>
    # ABML: cEOS OR cCR RTN
    ### s_EOS = (   cEOS,
    ###     kOR,    cCR,
    ###     kRTN
    ### )

    # ========================================
    # String or Array Definition - Used for DIM / COM.
    # (Quirk: DIM/COM with zero items will tokenize but not run.)
    # <NSMAT> = <TNVAR> ( <EXP> <NSMAT2> ) | <TSVAR> ( <EXP> ) <END>
    #   (Original code just has: "<NSMAT> = <TNVAR> ( <EXP> <NSMAT2> )")
    # ABML: ESRT.AD(TNVAR) cLPRN CHNG cDLPRN VEXP ANTV.AD(NMAT2) cRPRN OR ESRT.AD(TSVAR) cLPRN CHNG cDSLPR VEXP cRPRN RTN
    ### s_NSMAT = ( self.f_TNVAR, cLPRN, kCHNG, cDLPRN, s_EXP, s_NMAT2, cRPRN,
    ###     kOR,    self.f_TSVAR, cLPRN, kCHNG, cDSLPR, s_EXP, cRPRN,
    ###     kRTN
    ### )

    def f_NSMAT(self):
        # Faithful translation of ataribas.asm _NSMAT:
        #   _NSMAT = ESRT(_TNVAR), CHNG(CLPRN,CDLPRN), _VEXP, ANTV(_NMAT2), CRPRN, _OR
        #            ESRT(_TSVAR), CHNG(CLPRN,CDSLPR), _VEXP, CRPRN, _RTN
        # ESRT(_TNVAR)/ESRT(_TSVAR) emit the var token but do NOT consume '(' —
        # the '(' is matched here by f_CHNG(cLPRN, cDLPRN/cDSLPR), which emits the
        # dim-open token. Each OR-branch is wrapped in attempt() so a partial
        # numeric match rolls back before the string branch (mirrors _OR).
        # _VEXP -> f_EXP ; ANTV(_NMAT2) -> f_NMAT2 (optional comma'd dims) ;
        # CRPRN -> f_SRCONT(cRPRN).
        return (
            self.attempt(
                lambda: (self.f_TNVAR() and
                         self.f_CHNG(cLPRN, cDLPRN) and
                         self.f_EXP() and
                         self.f_NMAT2() and
                         self.f_SRCONT(cRPRN))
            )
            or
            self.attempt(
                lambda: (self.f_TSVAR() and
                         self.f_CHNG(cLPRN, cDSLPR) and
                         self.f_EXP() and
                         self.f_SRCONT(cRPRN))
            )
        )

    # Zero or More String/Array Definitions
    # <NSML> = <NSMAT> <NSML2> | <NADA> <END>
    # ABML: JS(NSMAT) JS(NSML2) OR RTN
    ### s_NSML = (  s_NSMAT, s_NSML2,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_NSML(self):
        return ((self.f_NSMAT() and self.f_NSML2())
            or self.f_NADA()     )

    # Another Numeric Array Variable with Dimension(s) or Nothing
    # <NSML2> = , <NSML> | <NADA> <END>
    # ABML: cCOM JS(NSML) OR RTN
    ### s_NSML2 = ( cCOM, s_NSML,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_NSML2(self):
        return ((self.f_SRCONT(cCOM) and self.f_NSML())
            or  self.f_NADA()    )

    # IF ... THEN ... Complete Statement
    # <IF> = <EXP> THEN <IFA> <EOS> <END>
    # ABML: VEXP cTHEN JS(IFA) JS(EOS) RTN
    ### s_IF = (s_EXP, cTHEN, s_IFA, s_EOS, kRTN)

    def f_IF(self):
        saved = self.snapshot()

        # Parse the condition expression
        if not self.f_EXP():
            self.restore(saved)
            return False

        # Must have THEN keyword after condition
        if not self.f_SRCONT(cTHEN):
            self.restore(saved)
            return False

        # Must have a valid body after THEN (either line number or statement)
        # f_IFA returns True and sets self._if_body_is_stmt to indicate whether
        # the body was a statement (which already consumed the trailing ':'
        # via its own f_EOS) or a line number (which did NOT).
        if not self.f_IFA():
            self.restore(saved)
            return False

        if self._if_body_is_stmt:
            # Statement body already consumed the ':' (or 0x9B) via its own
            # f_EOS. The IF rule must NOT consume a second separator here.
            # Accept if we are at end-of-buffer or at a line terminator;
            # otherwise the ':' was consumed and we now sit at the next
            # statement start, which is valid (multi-statement line).
            if not (self.index >= len(self.lbuff)
                    or (self.index < len(self.lbuff) and self.lbuff[self.index] == 0x9B)):
                # Not at a clean terminator — but the body handler consumed
                # the ':' so we're at the next statement. This is OK for a
                # multi-statement line. (We do NOT call f_EOS again, or we'd
                # double-consume the separator.)
                pass
        else:
            # Line-number body (TNCON) did not consume the separator, so the
            # IF rule owns the EOS here (matches assembly _IF: ... IFA EOS).
            if not (self.f_EOS() or self.index >= len(self.lbuff)):
                self.restore(saved)
                return False

        # All checks passed - append cCR for end of line
        self.f_append_cCR()
        return True

    # Following THEN, a Line Number expression or a Statement
    # See special notes about EIF.
    # <IFA> = <TNCON> | <EIF>
    # ABML: ESRT.AD(TNCON) OR ESRT.AD(EIF)
    ### s_IFA = (   self.f_TNCON,
    ###     kOR,    self.f_EIF)

    def f_IFA(self):
        # Following THEN, the body can be a line-number literal or a statement.
        # Sets self._if_body_is_stmt so f_IF knows whether the trailing ':'
        # was already consumed by the statement handler (statement case) or
        # still needs to be consumed by the IF rule (line-number case).
        self._if_body_is_stmt = False
        saved = self.snapshot()

        # Try TNCON (line number or expression) first
        if self.f_TNCON():
            self._if_body_is_stmt = False
            return True

        # Try EIF (nested IF statement / any statement body)
        if self.f_EIF():
            self._if_body_is_stmt = True
            return True

        # Neither worked - syntax error
        self.restore(saved)
        return False

    # ========================================
    # Arguments for PRINT
    # ========================================

    # ========================================
    # Numeric/String Expression Group
    #   or Separators plus Expression-Group-or-Nothing
    #   or Nothing
    # <PR1> = <PEL> | <PSL> <PR2> | <NADA> <END>
    # ABML: JS(PEL) OR JS(PSL) JS(PR2) OR RTN
    ### s_PR1 = (   s_PEL,
    ###     kOR,    s_PSL, s_PR2,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_PR1(self):
        return (self.f_PEL()
            or  (self.f_PSL() and self.f_PR2())
            or  self.f_NADA()    )

    # Numeric/String Expression Group, or Nothing
    # <PR2> = <PEL> | <NADA> <END>
    # ABML: JS(PEL) OR RTN
    ### s_PR2 = (   s_PEL,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_PR2(self): return self.f_PEL() or self.f_NADA()

    # Numeric/String Expression Group
    # <PEL> = <PES> <PELA> <END>
    # ABML: JS(PES) JS(PELA) RTN
    ### s_PEL = (s_PES, s_PELA, kRTN)

    def f_PEL(self): return self.f_PES() and self.f_PELA()

    # Numeric Expression or String Expression
    # <PES> = <EXP> | <STR>
    # ABML: VEXP OR ANTV.AS(STR) RTN
    ### s_PES = (   s_EXP,
    ###     kOR,    s_STR,
    ###     kRTN
    ### )

    def f_PES(self): return self.f_EXP() or self.f_STR() or self.f_STCOMP()

    # Separator plus Expression-Group-or-Nothing, or Nothing
    # <PELA> = <PSL> <PEL> | <NADA> <END>
    # ABML: JS(PSL) JS(PEL) OR RTN
    ### s_PELA = (  s_PSL, s_PEL,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_PELA(self): return (self.f_PSL() and self.f_PEL()) or self.f_NADA()

    # ========================================
    # One or More Print Separators
    # <PSL> = <PS> <PSLA> <END>
    # ABML: JS(PS) JS(PSLA) RTN
    ### s_PSL = (s_PS, s_PSLA, kRTN)

    def f_PSL(self): return self.f_PS() and self.f_PSLA()

    # Print Separator or Nothing
    # <PSLA> = <PSL> | <NADA> <END>
    # ABML: JS(PSL) OR RTN
    ### s_PSLA = (  s_PSL,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_PSLA(self): return self.f_PSL() or self.f_NADA()

    # Print Separator - Comma or Semicolon
    # <PS> = , | ; <END>
    # ABML: cCOM OR cSC RTN
    ### s_PS = (    cCOM,
    ###     kOR,    cSC,
    ###     kRTN
    ### )

    def f_PS(self): return self.f_SRCONT(cCOM) or self.f_SRCONT(cSC)

    # ========================================
    # List of Zero, One, or Two Expressions (e.g., for LIST)
    # <L1> = <EXP> <L2> | <NADA> <END>
    # ABML: VEXP JS(L2) OR RTN
    ### s_L1 = (    s_EXP, s_L2,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_L1(self): return (self.f_EXP() and self.f_L2()) or self.f_NADA()

    # One More Expression or Nothing
    # <L2> = ,<EXP> | <NADA> <END>
    # ABML: cCOM VEXP OR RTN
    ### s_L2 = (    cCOM, s_EXP,
    ###     kOR,    # nada #
    ###     kRTN
    ### )

    def f_L2(self): return (self.f_SRCONT(cCOM) and self.f_EXP()) or self.f_NADA()

    # ========================================
    # REM Statement takes the rest of the line. Note the lack of OR and RTN in this rule.
    # <REM> = <EREM>
    # ABML: ESRT.AD(EREM)
    ### s_REM = (f_EREM)

    def f_REM(self): return self.f_EREM()

    # DATA Statement takes the rest of the line
    # <DATA> = <EDATA>
    # ABML: ESRT.AD(EDATA)
    ### s_DATA = (f_EDATA)

    def f_DATA(self): return self.f_EDATA()

    # ========================================
    # ASC, VAL, or LEN Function Name
    # <NFSP> = ASC | VAL | LEN <END>
    # ABML: cASC OR cVAL OR cLEN RTN
    ### s_NFSP = (  cASC,
    ###     kOR,    cVAL,
    ###     kOR,    cLEN,
    ###     kRTN
    ### )

    def f_NFSP(self): return (self.f_SRCONT(cASC) or self.f_SRCONT(cVAL)
                              or self.f_SRCONT(cLEN) or self.f_SRCONT(cADR))

    # STR$ or CHR$ Function Name
    # <SFNP> = STR | CHR <END>
    # ABML: cSTR OR cCHR RTN
    ### s_SFNP = (  cSTR,
    ###     kOR,    cCHR,
    ###     kRTN
    ### )

    # ========================================
    # Unlimited Arguments for USR
    # <PUSR> = <EXP> <PUSR1> <END>
    # ABML: VEXP JS(PUSR1) RTN
    ### s_PUSR = (s_EXP, s_PUSR1, kRTN)             # Expr ...

    def f_PUSR(self): return self.f_EXP() and self.f_PUSR1()

    # Continuation of USR Arguments
    # <PUSR1> = ,<PUSR> | <NADA> <END>
    # ABML: cCOM CHNG cACOM JS(PUSR) OR RTN
    ### s_PUSR1 = ( cCOM, kCHNG, cACOM, s_PUSR,     # , ...
    ###     kOR,    # nada #                        # or Nothing
    ###     kRTN
    ### )

    def f_PUSR1(self):
        return ((self.f_CHNG(cCOM, cACOM) and self.f_PUSR())
            or self.f_NADA()     )

    # This object is initialized once. It restarts on each call to tokenize_statement
    def __init__(self, program=None) -> None:
        self.statement_syntax_table = (
            self.f_REM,      self.f_DATA,     self.f_INPUT,    self.f_COLOR,    self.f_LIST,
            self.f_ENTER,    self.f_LET,      self.f_IF,       self.f_FOR,      self.f_NEXT,
            self.f_GOTO,     self.f_GOTO,     self.f_GOSUB,    self.f_TRAP,     self.f_BYE,
            self.f_CONT,     self.f_COM,      self.f_CLOSE,    self.f_CLR,      self.f_DEG,
            self.f_DIM,      self.f_END,      self.f_NEW,      self.f_OPEN,     self.f_LOAD,
            self.f_SAVE,     self.f_STATUS,   self.f_NOTE,     self.f_POINT,    self.f_XIO,
            self.f_ON,       self.f_POKE,     self.f_PRINT,    self.f_RAD,      self.f_READ,
            self.f_RESTORE,  partial(self.f_RETURN), self.f_RUN,      self.f_STOP,     self.f_POP,
            self.f_PRINT,    self.f_GET,      self.f_PUT,      self.f_GR,       self.f_PLOT,
            self.f_POS,      self.f_DOS,      self.f_DRAWTO,   self.f_SETCOLOR, self.f_LOCATE,
            self.f_SOUND,    self.f_LPRINT,   self.f_CSAVE,    self.f_CLOAD,    self.f_ILET
        )

        self.rule_index_out = 0
        self.program = program

    # command_id - Token for the identified command we will be validating
    # lbuff - Text Buffer containing the input line we will be testing
    # index - Index in the buffer to tokenize from. Starts at the first command argument.
    # tokenized - Output buffer to append onto.
    # state_stack - Rules need to save and restore position during tokenization
    def tokenize_statement(self, command_id:int, lbuff:bytes, index:int, tokenized:bytearray):
        self.lbuff = lbuff
        self.index = index
        # MAXCIX is per LINE, not per statement, and the ROM never lowers it
        # (SYNTAX zeroes it once when a new line is read). Seed it from the
        # statement's own start so it always names a real position.
        if not hasattr(self, 'maxcix') or index > self.maxcix:
            self.maxcix = index
        self.tokenized = tokenized
        self.old_length = len(tokenized)
        self.state_stack = []
        self.statement_syntax_table[command_id]()

    def result(self) -> (int, bytearray):
        return (self.index, self.tokenized)

    # print(f_OP())
