#!/usr/bin/env python3
"""
basic.py - AtariBASIC language tokenizer and interpreter

Usage: basic.py [-l|--list] [-o|--output <outfile>] [infile]

AtariBASIC Playground

positional arguments:
  infile                Optional input file (LST or BAS).

options:
  -h, --help            show this help message and exit
  -l, --list            Just list the program and exit.
  -c, --clist           List the colorized program and exit.
  -a, --abbrev          Produce an abbreviated listing.
  -s, --struct          Display structured output.
  -t, --tvars           Print the VNT and VVT tables.
  -o OUTPUT, --output OUTPUT

Example:
        basic.py ATARIFOO.BAS -l -a | subl

This Python Script maintains a single Atari BASIC program in tokenized form and
provides an interface for the user to modify it. It is meant to run in the Terminal,
but will get a proper UI later on.

For validation compare the tokenization output against Atari's original tokenizer.
Set a breakpoint at SETCODE ($A2C8) to observe tokenization in action and copy its behavior.

# Modes of operation
- In standard mode behave like any language interpreter (e.g., Python) and take text input interactively.
- For a LST input file tokenize all the lines of the file then go to interactive mode.
- For a BAS input file load the BAS file into our data structures then go to interactive mode.

# Tokenizer
The tokenizer will closely mirror the implementation from the original BASIC cartridge
(fixing a bug or two), in order to produce an exact BAS file from an input LST file.
For testing I'll rig a Makefile that uses `atari800` to do the tokenization for us,
placing the result in the shared HardDrive1 folder.

# Program Executor
The Atari BASIC Source Book covers a lot of this. We'll just lay out the basics:
- The "Program" refers to the entire Statement Table (ST), a packed binary buffer containing line
  numbers, chunk sizes, and tokens: [ [line16][nextLine][ [nextStatement][command][(etc)][eos|eol] ]... ]...
- Memory buffers contain the state of the interpreter itself:
  - Current Offset in the Program
- Variables contain the broad state of the interpreted program
  - The Variable Name Table (VNT) is used in listing
  - The Variable Value Table is modified by the interpreter in response to the Program

# Development Plan
- Phase 1: Program Loader so we can LOAD and LIST a tokenized BAS file: load_file_BAS, list_program
- Phase 2: Interactive Mode and Program Tokenizer. Take input, tokenize, SAVE to BAS and LST files.
- Phase 3: Integrate into the Sublime plugin to validate and tokenize AtariBASIC directly.
- Phase 4: Program Runner to actually execute statements and complete programs.

"""
import re, os, sys, math, argparse
# from termcolor import colored  # Temporarily disabled for testing

def colored(text, color=None):
    """Simple fallback - just return text without colors."""
    return text

from pathlib import Path
from typing import Callable, Dict, Iterable

from ataridefs import *
from atascii import *
from ataribasic import *
from statement_table import StatementTable  # NEW: Import StatementTable class

# Syntaxer for tokenization
from ataribasic_syntax import *

# --------------------------------------------------------------------------- #

DEBUG_CODE = False

# Per-line tokenize failures from the most recent load_file_LST() call.
# load_file_LST prints them, but --json redirects stdout to a sink, so the
# Inspector needs them in the JSON document or it shows an empty pane with
# error:null and no indication anything failed.
LOAD_ERRORS = []

# Output index of the displacement byte written by the most recent
# tokenize_statement() call, so the multi-statement loop can adjust it when it
# pops that statement's trailing cCR.
LAST_DISP_INDEX = None

args_abbrev = False
args_colorify = False
list_colorify = False

# ANSI color formatting
# black, grey, red, green, yellow, blue, magenta, cyan, light_..., white
DARK_MODE = False
def color_print(text, color, end=None):
    if DARK_MODE:
       if color == 'black': color = 'white'
       elif not (color.startswith('light_') or color == 'white'):
           color = 'light_' + color
    #elif color == 'white':
    #    color = 'black'

    print(colored(text, color), end=end)

# Colorize only if the flag is set
def colorize(text, color=None, end=None):
    if list_colorify and color:
        color_print(text, color, end=end)
    else:
        print(text, end=end)

class Program:
    """
    Encapsulates an Atari BASIC program: the VNT, VVT, ST,
    runtime stack, and all helper methods for loading, listing,
    tokenising, etc.
    """

    _VTYPE_STRING = 0x80
    _VTYPE_ARRAY = 0x40
    _VTYPE_STRING_ARRAY = 0x41
    _VTYPE_NUMERIC = 0x00

    def __init__(self) -> None:
        # Core program data
        self.vnt = []         # MSB-terminated names
        self.vvt = []         # 8-byte entries
        self.strings_and_arrays = bytearray()

        # Runtime state
        self.program_stack = []
        self.current_statement = None

        # Statement table - use StatementTable class for linked list management
        self.statement_table = StatementTable()

        # Tokenizer bookkeeping
        self._var_index_by_name = {}  # normalised -> vnt index

        # Variable name table - initialized here for interactive mode
        self.variable_name_table = []

        # For interactive mode: track variable names by index
        self.vnt_names = []  # List of variable names (A, B, C, etc.)

    # Variable name helpers -----------------------------------------------

    def _snapshot(self):
        return (
            list(self.vnt),
            list(self.vvt),
            dict(self._var_index_by_name),
        )

    def _restore_snapshot(self, snap):
        self.vnt, self.vvt, self._var_index_by_name = snap

    def _ensure_variable(self, normalised):
        """Ensure a variable exists in the VNT. Return its index."""
        normalised = normalised.upper()
        if normalised not in self._var_index_by_name:
            idx = len(self.vnt)
            self.vnt.append(normalised)

            # Track variable names for display in interactive mode
            if idx < len(self.vnt_names):
                self.vnt_names[idx] = normalised
            else:
                # Extend vnt_names if needed
                while len(self.vnt_names) <= idx:
                    self.vnt_names.append("")
                self.vnt_names[idx] = normalised

            self.vvt.append(None)
            self._var_index_by_name[normalised] = idx
        return self._var_index_by_name[normalised]

    def _create_numeric_variable(self, normalised):
        idx = self._ensure_variable(normalised)
        self.vvt[idx] = [Program._VTYPE_NUMERIC, bytes(6)]
        return idx

    def _create_string_variable(self, normalised):
        idx = self._ensure_variable(normalised)
        self.vvt[idx] = [Program._VTYPE_STRING, bytes(6)]
        return idx

    def _create_array_variable(self, normalised):
        idx = self._ensure_variable(normalised)
        self.vvt[idx] = [Program._VTYPE_ARRAY, bytes(6)]
        return idx

    def ingest_variable_name_table(self, vnt_data):
        """
        Extract variable names from the VNT buffer.
        Return the extracted VNT array.
        """
        vnt = []
        name_bytes = bytearray()            # Temporary holder

        for b in vnt_data:
            if b & 0x80:                    # High bit set -> end of name
                name_bytes.append(b & 0x7F) # Clear high bit
                vnt.append(name_bytes.decode('ascii'))
                name_bytes.clear()          # Start a new name
                #print(f"Variable {len(vnt)-1}: '{varname}'")
            else:
                name_bytes.append(b)        # Accumulate the name bytes

        # For now ignore any trailing incomplete name
        self.variable_name_table = vnt

    def variable_name(self, token):
        """Return the variable name for the given variable token."""
        i = token & 0x7F

        # For interactive mode, check vnt_names first
        if hasattr(self, 'vnt_names') and i < len(self.vnt_names):
            return self.vnt_names[i]

        # Fallback to VNT for loaded .BAS files (if vnt_names not populated)
        if i < len(self.variable_name_table):
            return re.sub(r'[\(]', '', self.variable_name_table[i])

        return f"<var#{i}>"

    def ingest_variable_value_table(self, vvt_data):
        """Extract variable dimensions and values from the VVT buffer."""
        vvt = []
        val_bytes = bytearray()                         # Temporary holder

        for i in range(0, len(vvt_data), 8):
            chunk = vvt_data[i:i+8]

            if len(chunk) != 8:
                raise ValueError(f"Incomplete 8‑byte entry at offset {vvt_offset + i}")

            var_type = chunk[0] & 0xC0                  # High 2 bits: type
            var_id = chunk[1] & 0x7F                    # Low 7 bits: variable number
            item = { 'type': var_type, 'id': var_id }
            if var_type == 0x40:
                # Array: disp, dim1, dim2
                item['disp'] = getint(chunk[2:4])    # Displacement in string/array memory
                item['dim1'] = getint(chunk[4:6])    # Array dimension 1
                item['dim2'] = getint(chunk[6:8])    # Array dimension 2 (or 1)
                #print(f"Array {prog.variable_name(var_id)}({item['dim1']}, {item['dim2']}) [{item['disp']}]")
            elif var_type == 0x80:
                # String: disp, curl, maxl
                item['disp'] = getint(chunk[2:4])    # Displacement in string/array memory
                item['curl'] = getint(chunk[4:6])    # Current length
                item['maxl'] = getint(chunk[6:8])    # Max length as indicated by DIM
                #print(f"String {prog.variable_name(var_id)}({item['maxl']}) [{item['disp']}] = \"{'.'*item['curl']}\"")
            else:
                item['value'] = decode_bcd(chunk[2:])
                #print(f"Scalar {prog.variable_name(var_id)} = {item['value']}")

            vvt.append(item)

        # If the file is malformed and we ended mid‑name, we could handle it here.
        # For now we ignore any trailing incomplete name.
        self.variable_value_table = vvt

    def print_variable_tables(self):
        """Print all the variables with their types and sizes.

        Handles BOTH variable models:
          - BAS-loading path: ingest_variable_table() builds
            self.variable_value_table, a list of dicts with keys
            type/disp/dim1/dim2/maxl/curl/value.
          - LST-tokenizing path: self.vnt (names) + self.vvt (entries of
            [type, bytes]). No .variable_value_table attribute exists, which
            previously made `basic.py FILE -t` crash with AttributeError.
        """
        vvt = getattr(self, 'variable_value_table', None)

        if vvt:
            print("Variables:")
            for i, v in enumerate(vvt):
                vname = self.variable_name(i)
                disp = v.get('disp', -1)
                dstr = f"[{v['disp']:04X}]" if disp >= 0 else "      "
                print(f"${(i|0x80):02X} {dstr} {vname}", end='')
                t = v['type']
                if t == 0x40:
                    print(f"({v['dim1']}, {v['dim2']})")
                elif t == 0x80:
                    print(f"({v['maxl']}) = \"{'.'*v['curl']}\"")
                else:
                    print(f" = {v['value']}")
            print()
            return

        # Tokenizer-side model: names in self.vnt, types in self.vvt.
        names = getattr(self, 'vnt', []) or []
        types = getattr(self, 'vvt', []) or []
        if not names:
            print("No Variables")
            print()
            return

        print("Variables:")
        for i, name in enumerate(names):
            entry = types[i] if i < len(types) else None
            vtype = entry[0] if entry else Program._VTYPE_NUMERIC
            if vtype == Program._VTYPE_ARRAY:
                kind = "array"
            elif vtype == Program._VTYPE_STRING:
                kind = "string"
            elif vtype == Program._VTYPE_STRING_ARRAY:
                kind = "string array"
            else:
                kind = "numeric"
            status = "" if entry else "   (declared, no VVT entry)"
            print(f"${(i|0x80):02X}        {name:<12} {kind}{status}")
        print()

    def ingest_statement_table(self, st_data):
        """
        Load statement table data from a .BAS file.

        Args:
            st_data: Raw statement table bytes (already extracted from BAS file)
        """
        # Create StatementTable and populate it with the raw data
        self.statement_table = StatementTable()
        self.statement_table.data = bytearray(st_data)

    def insert_statement(self, statement) -> None:
        """
        Insert a tokenized statement into the program.

        Args:
            statement: Tokenized statement as bytearray [ln_lo, ln_hi, len, ...]
        """
        self.statement_table.insert_statement(getint(statement[0:2]), statement)

    def _reset_variable_state(self) -> None:
        self.variable_name_table = []
        self.variable_value_table = []
        self.strings_and_arrays = []
        self.program_stack = []
        self._var_index_by_name.clear()

# A class to contain the static tokenized BASIC program, variable names, etc.
prog = Program()

# A class to validate and tokenize a complete line of Atari BASIC, which can then be added to the Program
syntaxer = Syntaxer(prog)

# Symbols used by the original tokenizer:

# LOMEM (80, 81) points to this 256 byte buffer (at the end of OS RAM):
tokenized_line: bytearray = bytearray()

# --------------------------------------------------------------------------- #
# Immediate Command Handlers
# Interpret our own basic.py commands
# --------------------------------------------------------------------------- #

def h_REM()  -> None: print("OK")
def h_DATA() -> None: print("OK")
def h_LOAD() -> None: print("OK")
def h_SAVE() -> None: print("OK")

def h_DOS() -> None: x_DOS()
def h_BYE() -> None: x_BYE()

# NEW - Clear the program and reset state
def h_NEW() -> None:
    global prog, syntaxer
    prog = Program()  # Create a fresh program
    syntaxer.program = prog  # Update the syntaxer's reference to the new program

def x_NEW() -> None:
    # Call h_NEW for consistency
    h_NEW()

def two_int_args(args):
    if args:
        m = re.match(r'(\d+),? *(\d*)', args)
        r1 = int(m[1])
        r2 = int(m[2]) if m[2] else r1
    else:
        r1, r2 = 0, 32767
    return r1, r2

def do_LIST(args=None, colorify=False):
    start, end = two_int_args(args)
    list_program_from_st(start=start, end=end, abbrev=args_abbrev, colorify=colorify)

def h_LIST(args=None) -> None:
    do_LIST(args)

def h_CLIST(args=None) -> None:
    do_LIST(args, True)

# --------------------------------------------------------------------------- #
# Statement Execution
# --------------------------------------------------------------------------- #

def x_REM()      -> None: pass
def x_DATA()     -> None: pass
def x_INPUT()    -> None: pass
def x_COLOR()    -> None: pass
def x_LIST()     -> None: pass
def x_ENTER()    -> None: pass
def x_LET()      -> None: pass
def x_IF()       -> None: pass
def x_FOR()      -> None: pass
def x_NEXT()     -> None: pass
def x_GOTO()     -> None: pass
def x_GOSUB()    -> None: pass
def x_TRAP()     -> None: pass

# XBYE - Execute BYE
def x_BYE():
    exit(0)

def x_CONT()     -> None: pass
def x_COM()      -> None: pass
def x_CLOSE()    -> None: pass
def x_CLR()      -> None: pass
def x_DEG()      -> None: pass
def x_DIM()      -> None: pass
def x_END()      -> None: pass

# --------------------------------------------------------------------------- #
# LIST from prog.st (for LST loading and interactive mode)
# --------------------------------------------------------------------------- #

def format_bcd_value(value):
    """Format a decoded BCD float the way Atari BASIC LISTs it.

    Atari prints integral values without a trailing '.0' (e.g. 10, not 10.0)
    and uses a minimal floating-point representation otherwise.
    """
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    # Non-integral: use a compact representation (no exponent unless huge)
    return repr(value)


def list_program_from_st(start=0, end=32767, abbrev=args_abbrev, colorify=None):
    """
    Implement the LIST command to list the program in human-readable form.
    Reads from prog.statement_table.data (StatementTable linked list).
    For 'abbrev' output abbreviated commands (e.g., "D." rather than "DATA").

    Spacing follows the Atari BASIC LIST routine (ataribas.asm LLINE/LSTMT/
    LPRTOKEN/LPTWB): the line number is followed by one blank; a statement
    command keyword is followed by one blank; punctuation operators (+, =, (,
    ), ,, :, etc.) are emitted with no surrounding blanks; alphabetic word
    operators (AND/OR/NOT) get a blank on each side; functions (ABS, RND,
    CHR$, ...) get no blanks. Implied-LET (kILET) prints no keyword.
    """
    if DEBUG_CODE: print(f"DEBUG: list_program_from_st called, start={start}, end={end}")
    if prog is None or len(prog.statement_table.data) == 0: return

    from ataribasic import decode_bcd
    from ataridefs import cFFUN
    qsp = "" if abbrev else " "

    global list_colorify
    list_colorify = colorify if colorify else args_colorify

    # Loop through the program's lines
    count = 0
    for lineno, stmt in prog.statement_table.list_program(start, end):
        count += 1
        if DEBUG_CODE: print(f"DEBUG: Processing line {lineno}, stmt_len={len(stmt)}")

        # Print the line number (followed by exactly one blank, per LLINE/_LPTTB)
        colorize(f"{lineno}", color=token_color['number'], end=' ')

        # Detokenize the statement body.
        # Layout: [ln_lo ln_hi] line_len  disp cmd ... disp cmd ...
        # Offset 3 is the FIRST statement's displacement byte, so the command
        # token is at 4. Each subsequent statement is likewise preceded by its
        # own displacement, skipped in the statement loop below.
        i = 4
        # Locate the statement terminator so we can suppress a trailing blank
        # on the final token of the line. REM/DATA end with a literal CR
        # (0x9B); every other statement ends cCR (0x16).
        cr_pos = len(stmt) - 1
        while cr_pos > i and stmt[cr_pos] not in (0x16, 0x9B):
            cr_pos -= 1

        # Walk the statements using the DISPLACEMENT bytes rather than hunting
        # for cEOS (0x14). Scanning for 0x14 is unsafe: it is also a legitimate
        # token value inside a statement (DIM is 0x14, and it appears in
        # operand streams), so a value-based scan mis-splits lines. Each
        # statement's displacement points just past its own last byte, which is
        # exactly where the next statement's displacement lives.
        stmt_starts = []
        p = 3
        while p < len(stmt):
            disp = stmt[p]
            stmt_starts.append(p + 1)       # command token follows the disp
            if disp <= p or disp > len(stmt):
                break                       # malformed; stop rather than loop
            p = disp

        for si, start in enumerate(stmt_starts):
            if si:
                colorize(':', color=token_color['command'], end='')
            i = start
            cmd_tok = stmt[i] ; i += 1

            # REM, DATA, ERROR -- emit the command keyword (if any) then the
            # rest of the line verbatim as ATASCII text.
            is_fluff = cmd_tok in (kREM, kDATA, kERROR)
            tok_type = 'data' if is_fluff else 'command'

            tstr = string_for_command_token(cmd_tok, abbrev)
            if tstr != "":
                # Suppress the trailing blank if this is the last token on the line.
                sp = "" if (i >= cr_pos) else qsp
                colorize(tstr, color=token_color[tok_type], end=sp)

            if is_fluff:
                rest = stmt[i:cr_pos] if i < cr_pos else b''
                colorize(atascii_to_unicode_str(rest), token_color['data'], end='')
                break

            # Emit tokens until the end of THIS statement. The bound is the
            # statement's own displacement (stmt_end), not len(stmt), so a
            # multi-statement line does not bleed into the next statement.
            # Stop one byte short of a trailing cEOS: the ':' separator is
            # printed by the statement loop above, so emitting it here too
            # would double it.
            stmt_end = stmt[start - 1] if start - 1 < len(stmt) else len(stmt)
            stmt_end = min(stmt_end, len(stmt))
            if stmt_end and stmt[stmt_end - 1] == 0x14:
                stmt_end -= 1
            while i < stmt_end:
                # String literal (0x0F) -- length byte then characters
                if stmt[i] == 0x0F:
                    strlen = stmt[i+1] ; i += 2
                    str_bytes = bytes(stmt[i:i+strlen]) if i + strlen <= len(stmt) else b''
                    unicode_str = atascii_to_unicode_str(str_bytes)
                    colorize('"' + unicode_str + '"', token_color['string'], end='')
                    i += strlen
                    continue

                tok = stmt[i] ; i += 1

                if tok == 0x16:  # cCR -- end of statement
                    break

                # Variable token (MSB set) -- expand the name (incl. '$')
                if tok & 0x80:
                    colorize(prog.variable_name(tok), color=token_color['variable'], end='')
                    continue

                # BCD literal (0x0E): next 6 bytes are the value
                if tok == 0x0E:
                    bcd_bytes = stmt[i:i+6] if i + 5 < len(stmt) else bytes(6)
                    bcd_value = decode_bcd(bcd_bytes)
                    colorize(format_bcd_value(bcd_value), color=token_color['number'], end='')
                    i += 6
                    continue

                # Command token (0-31) -- check BEFORE operators so overlaps
                # (e.g. 34 = kREAD vs cEQ) resolve to the command form here,
                # but only when this is a statement-leading command. We already
                # consumed cmd_tok above, so any 0-31 token seen mid-statement
                # is an operator/function range instead.
                if tok < len(commands_info):
                    # A command token appearing mid-statement is actually an
                    # operator/function token (e.g. cCOM, cEOS). Fall through.
                    pass

                # Operator / Function token (0x0B .. 0x7F)
                if tok < len(ops_and_funcs):
                    op_name = (ops_and_funcs[tok] or "UNKNOWN")
                    name = op_name.strip()
                    is_alpha_op = name.isalpha() and tok < cFFUN
                    if is_alpha_op:
                        # Alphabetic word-operator (AND/OR/NOT): blank on each side.
                        sp = "" if (i >= cr_pos) else qsp
                        colorize(' ' + name, color=token_color['function'], end=sp)
                    else:
                        # Punctuation operator or function: no surrounding blanks.
                        colorize(name, color=token_color['function'], end='')
                    continue

                # Unknown token
                colorize(f"??{tok:02X}??", 'red', end='')

        print()

        if DEBUG_CODE: print(f"DEBUG: Finished line {lineno}")

    if DEBUG_CODE: print(f"DEBUG: list_program_from_st done, processed {count} lines")


# Command dispatch table
# --------------------------------------------------------------------------- #

COMMAND_HANDLERS: Dict[str, Callable[[], None]] = {
    "NEW":   h_NEW,
    "LOAD":  h_LOAD,
    "LIST":  h_LIST,
    "CLIST": h_CLIST,
    "SAVE":  h_SAVE,
    "DOS":   h_DOS,
    "BYE":   h_BYE,
}

def h_NEW() -> None:
    global prog, syntaxer
    prog = Program()  # Create a fresh program
    syntaxer.program = prog  # Update the syntaxer's reference to the new program
def x_OPEN()     -> None: pass
def x_LOAD()     -> None: pass
def x_SAVE()     -> None: pass
def x_STATUS()   -> None: pass
def x_NOTE()     -> None: pass
def x_POINT()    -> None: pass
def x_XIO()      -> None: pass
def x_ON()       -> None: pass
def x_POKE()     -> None: pass
def x_PRINT()    -> None: pass
def x_RAD()      -> None: pass
def x_READ()     -> None: pass
def x_RESTORE()  -> None: pass
def x_RETURN()   -> None: pass
def x_RUN()      -> None: pass
def x_STOP()     -> None: pass
def x_POP()      -> None: pass
def x_PRINT()    -> None: pass
def x_GET()      -> None: pass
def x_PUT()      -> None: pass
def x_GRAPHICS() -> None: pass
def x_PLOT()     -> None: pass
def x_POSITION() -> None: pass

# XDOS - Exit to DOS
def x_DOS():
    #do_CLSALL() # Close IOCB 1-7
    #jmp (DOSLOC)
    exit(0)

def x_DRAWTO()   -> None: pass
def x_SETCOLOR() -> None: pass
def x_LOCATE()   -> None: pass
def x_SOUND()    -> None: pass
def x_LPRINT()   -> None: pass
def x_CSAVE()    -> None: pass
def x_CLOAD()    -> None: pass
def x_ILET()     -> None: pass
def x_ERROR()    -> None: pass

# Statement Execution Table
# - Contains Statement Execution refs
# - Must be in same order as Statement Name Table
handler_table = [
    x_REM,      x_DATA,     x_INPUT,    x_COLOR,    x_LIST,
    x_ENTER,    x_LET,      x_IF,       x_FOR,      x_NEXT,
    x_GOTO,     x_GOTO,     x_GOSUB,    x_TRAP,     x_BYE,
    x_CONT,     x_COM,      x_CLOSE,    x_CLR,      x_DEG,
    x_DIM,      x_END,      x_NEW,      x_OPEN,     x_LOAD,
    x_SAVE,     x_STATUS,   x_NOTE,     x_POINT,    x_XIO,
    x_ON,       x_POKE,     x_PRINT,    x_RAD,      x_READ,
    x_RESTORE,  x_RETURN,   x_RUN,      x_STOP,     x_POP,
    x_PRINT,    x_GET,      x_PUT,      x_GRAPHICS, x_PLOT,
    x_POSITION, x_DOS,      x_DRAWTO,   x_SETCOLOR, x_LOCATE,
    x_SOUND,    x_LPRINT,   x_CSAVE,    x_CLOAD,    x_ILET,
    x_ERROR
]


# --------------------------------------------------------------------------- #
# BASIC listing
# --------------------------------------------------------------------------- #

token_color = {
    'command':  'yellow',
    'function': 'light_red',
    'number':   'white',
    'string':   'light_blue',
    'variable': 'green',
    'data':     'grey',
    'comment':  'black'
}

def emit_arg_rest_of_line(start, end):
    """The command argument is the rest of the line"""
    colorize(atascii_to_unicode_str(prog.statement_table[start:end]), token_color['data'], end='')
    pass

def emit_arg_rest_of_line_st(start, end):
    """The command argument is the rest of the line - reads from prog.statement_table.data"""
    colorize(atascii_to_unicode_str(prog.statement_table.data[start:end]), token_color['data'], end='')
    pass

# For structured output use an indent
args_structured = False
indent = ""

def emit_token_at_index(i):
    """Emit the token at the given index in the statement_table"""
    atok = prog.statement_table.data[i]
    if DEBUG_CODE: print(f"<{atok:02X}>", end='')

    # 80-FF Variable ID
    if atok & 0x80:
        colorize(prog.variable_name(atok), color=token_color['variable'], end='')
        return 1

    # 14 (20) End of Statement - colon separator
    if atok == 0x14:
        colorize(':', color=token_color['operator'], end='')
        return 1

    # 16 (22) End of Last Statement - newline
    if atok == 0x16:
        colorize('\n', color=token_color['operator'], end='')
        return 1

    # 0-31 Command tokens - look up in commands_info
    if atok < len(commands_info):
        # Special handling for TNCON (0x02) which is followed by variable ID
        if atok == 0x02:
            # TNCON is followed by a variable ID (80-FF range)
            var_id = prog.statement_table.data[i + 1] if i + 1 < len(prog.statement_table.data) else 0
            colorize(prog.variable_name(var_id), color=token_color['variable'], end='')
            return 2

        cmd_name = commands_info[atok].get('name', 'UNKNOWN')
        colorize(cmd_name, color=token_color['command'], end='')
        return 1

    # Advance past token
    i += 1

    # 0E (14) BCD Literal, Next 6 bytes
    if atok == 0x0E:
        bcd_bytes = prog.statement_table.data[i:i+6]
        bcd_value = decode_bcd(bcd_bytes)
        colorize(str(bcd_value), color=token_color['number'], end='')
        return 7

    # 0F (15) String Literal, Next byte is length
    if atok == 0x0F:
        strlen = prog.statement_table.data[i]
        str_bytes = prog.statement_table.data[i+1:i+1+strlen]
        colorize('"' + atascii_to_unicode_str(str_bytes).decode('utf-8') + '"', color=token_color['string'], end='')
        return strlen + 2

    # 0B (11) Comma separator
    if atok == 0x0B:
        colorize(', ', color=token_color['operator'], end='')
        return 1

    # 32-127 Operator/Function tokens
    if atok < len(ops_and_funcs):
        op_name = ops_and_funcs[atok] or "UNKNOWN"
        colorize(op_name, color=token_color['function'], end='')
        return 1

    # Unknown token
    colorize(f"??{atok:02X}??", 'red', end='')
    return 1

def emit_token_at_index_st(i):
    """Emit the token at the given index in prog.statement_table.data"""
    if DEBUG_CODE: print(f"DEBUG emit_token_at_index_st: i={i}, len(prog.statement_table.data)={len(prog.statement_table.data)}")
    atok = prog.statement_table.data[i]
    if DEBUG_CODE: print(f"<{atok:02X}>", end='')

    # 80-FF Variable ID
    if atok & 0x80:
        colorize(prog.variable_name(atok), color=token_color['variable'], end='')
        return 1

    # 14 (20) End of Statement
    #if atok == 0x14:
    #    print(ops_and_funcs[atok], end='')
    #    return 1

    # 16 (22) End of Last Statement
    #if atok == 0x16:
    #    print(ops_and_funcs[atok], end='')
    #    return 1

    # 0-31 Command tokens
    if atok < len(commands_info):
        cmd_name = commands_info[atok].get('name', 'UNKNOWN')
        colorize(cmd_name, color=token_color['command'], end='')
        return 1

    # 32-127 Operator/Function tokens
    if atok < len(ops_and_funcs):
        op_name = ops_and_funcs[atok] or "UNKNOWN"
        colorize(op_name, color=token_color['function'], end='')
        return 1

    # Unknown token
    colorize(f"??{atok:02X}??", 'red', end='')
    return 1

    # Advance past token
    i += 1

    # 0E (14) BCD Literal, Next 6 bytes
    if atok == 0x0E:
        bcd_bytes = prog.statement_table.data[i:i+6]
        bcd_value = decode_bcd(bcd_bytes)
        colorize(bcd_value, color=token_color['number'], end='')
        return 7

    # 0F (15) String Literal, Next byte is length
    if atok == 0x0F:
        strlen = prog.statement_table.data[i]
        str_bytes = prog.statement_table[i+1:i+1+strlen]
        colorize('"' + atascii_to_unicode_str(str_bytes) + '"', color=token_color['string'], end='')
        return strlen + 2

    is_func = atok >= 0x3D
    color_type = 'function' if is_func else 'command'

    # Other operators and functions
    colorize(op_func_string(atok), color=token_color[color_type], end='')
    return 1

def list_program(start=0, end=32767, abbrev=args_abbrev, colorify=None):
    """
    Implement the LIST command to list the program in human-readable form.
    For 'abbrev' output abbreviated commands (e.g., "D." rather than "DATA")
    """

    stend = len(prog.statement_table)
    if stend == 0: return

    qsp = "" if abbrev else " "

    # Loop through the statement table and interpret the data of each line.

    # Start at the first byte of the program
    i = 0

    # Global state during listing
    global list_colorify
    list_colorify = colorify if colorify else args_colorify

    # Loop through the program's lines
    while True:
        # The first two bytes are the little-endian line number
        lineno = getint(prog.statement_table[i:i+2])
        if lineno > end: break

        # The next byte is the offset to the next line
        line_len = prog.statement_table[i+2]
        nextline = i + line_len

        # Reached the first listing line yet?
        if lineno < start: i = nextline ; continue

        # Remember the index of the start of the line
        thisline = i

        # Print out the line number and detokenize the rest of the line
        #print(lineno, end=' ')

        if DEBUG_CODE: print(f"{{{line_len}}}", end='')
        colorize(f"{lineno}", color=token_color['number'], end=' ')

        # Skip to the first statement
        i += 3

        # Detokenize statements
        while i < nextline:
            # Get the statement offset from start of line
            st_off = prog.statement_table[i] ; i += 1
            if DEBUG_CODE: print(f"{{{st_off}}}", end='')
            next_st = thisline + st_off

            # Get the command token
            cmd_tok = prog.statement_table[i] ; i += 1
            if DEBUG_CODE: print(f"<{cmd_tok:02X}>", end='')

            # REM, DATA, ERROR
            is_fluff = cmd_tok in (kREM, kDATA, kERROR)
            tok_type = 'data' if is_fluff else 'command'

            # Print the command (if it's not "implied LET")
            tstr = string_for_command_token(cmd_tok, abbrev)
            if tstr != "": colorize(tstr, color=token_color[tok_type], end=qsp)

            # REM, DATA, ERROR
            if is_fluff:
                emit_arg_rest_of_line(i, nextline-1)
                i = nextline                    # Go right to the next line
                break

            # Emit tokens until the end of the statement
            # NOTE: Output for DIM statement needs to insert commas between items
            while i < next_st:
                # Emit the token, receiving the size of the emitted token
                tlen = emit_token_at_index(i)
                if DEBUG_CODE: print(f"[{tlen}]", end='')
                i += tlen

                #if i >= nextline:                   # At the next line?
                #    if DEBUG_CODE: print("~~~", end='')
                #    break

        #print(f" ({i})", end='')

        # Go to the next line. (Needed until tokenizer is complete.)
        i = nextline

        #print(f" ({i})")

        print()
        if i >= len(prog.statement_table): break

    list_colorify = False

# --------------------------------------------------------------------------- #
# BASIC tokenization
# --------------------------------------------------------------------------- #

def add_variable(var_name):
    """
    Variables are added as soon as they are parsed in a statement, so
    the statement only needs to contain the variable ID.
    """
    pass

def identify_keyword(statement_txt):
    """
    Scan the input string until a keyword is identified.
    Recognize abbreviations of keywords (ending with '.') over a minimum abbrev. length.
    If the input matches no keywords assume Implied LET ($36).
    If the input can't be a keyword, return ERROR ($37).
    """
    pass

# SKBLANK, SKBLANKS, SKPBLANK
def skip_blanks(inbuf, index):
    """
    Scan the input text starting at the given index.
    Return the index of the first non-blank character.
    """
    while index < len(inbuf) and inbuf[index] == ord(' '):
        index += 1
    return index

def delete_line(line_no):
    """
    Delete a tokenized line from the Statement Table
    """
    # TODO:
    # - Scan the statement table to find the line with the given number.
    # - Get the length of that line.
    # - Contract the Statement Table to chop out the line.
    print(f"Deleting line {line_no}")
    pass

# $A1C3 SYNENT - Evaluate command arguments with a BNF evaluator
def run_bnf_for_command(tokenized:bytearray, command_id:int, lbuff:bytes, index:int):
    """
    Run the syntaxer on the identified command and return the updated index.
    The tokenized buffer is updated in-place by the Syntaxer.
    """
    # The Syntaxer tokenize_statement already wrote to self.tokenized (= tokenized)
    # and run_bnf_for_command was supposed to use the result, but currently discards it.
    # The tokenized buffer was already updated by syntaxer.tokenize_statement above.
    index, _ = syntaxer.result()
    return index

def tokenize_statement(tokenized:bytearray, lbuff:bytes, index:int):
    """
    Tokenize a statement starting with a command

    Parameters
    ----------
    lbuff : bytes
        The raw text of the BASIC line to be tokenized.

    Returns
    -------
    The new index and the bytes of the tokenized statement
    """

    # $A0C1
    # Skip blanks before the command, exactly as the ROM's statement loop does:
    # @SYN0 calls SKBLANKS *before* saving STMSTRT and dispatching (ataribas.asm
    # ~line 347). Without this, a statement after ':' that begins with a space
    # (e.g. `IF A=1 THEN PRINT "A": PRINT "B"`) fails to match any command,
    # search_statement_name_table returns kERROR with the index UNCHANGED, and
    # the multi-statement loop in tokenize_line() spins forever on that index.
    while index < len(lbuff) and lbuff[index] in (32, 9):
        index += 1

    # Scan for a recognized command. Only (uppercase) letters are used in commands.
    # The command ID will be used to look up the BNF.
    command_id, index = search_statement_name_table(lbuff, index)

    # Save statement start position for the length byte.
    #
    # EVERY statement carries its own displacement byte, not just the first.
    # The ROM's SYN1 (ataribas.asm ~368) runs once per statement and does
    #     stmlbd = cox; _setcode(cox);      ; dummy for stmt length
    # then SYNOK (~461) writes it back:
    #     outbuff[stmlbd] = cox;            ; displacement to end of stmt
    # and loops back to SYN1 while the last input char was not CR.
    #
    # So `1002 I.A:IN.A` is  [ln_lo ln_hi] disp cmd ... disp cmd ...
    # with disp measured from the start of the LINE (it is a copy of COX, the
    # output index), i.e. it points just past the end of that statement.
    #
    # We previously emitted a single length byte at tokenized[2] and
    # overwrote it per statement. Detokenizers then read the SECOND
    # statement's command token where a displacement belonged, and rendered
    # every statement after the first as 'UNKNOWN'.
    disp_index = len(tokenized)
    tokenized.append(0)          # placeholder, patched below (ROM: _setcode)

    # Save statement start position for the length byte.
    cmd_index = len(tokenized)

    # The command token is part of the statement.
    tokenized.append(command_id)

    # Run the syntaxer on the statement for the identified command.
    syntaxer.tokenize_statement(command_id, lbuff, index, tokenized)

    # DEBUG: Check if the syntaxer succeeded by looking for cCR (22) in tokenized output
    if DEBUG_CODE: print(f"DEBUG tokenize_statement: After syntaxer, tokenized={bytes(tokenized).hex()}, cCR present? {22 in tokenized}")

    # Check if the syntaxer succeeded by looking for a statement terminator.
    # Normal statements end cCR (0x16); REM/DATA payloads end with a literal
    # CR (0x9B) -- see f_XDATA and ROM _XDATA. Accept either, or REM/DATA
    # lines are rejected with error 6.
    if 22 not in tokenized and 0x9B not in tokenized:
        # Statement failed to tokenize - restore to just the command token.
        # Drop the displacement placeholder too, not only the command.
        del tokenized[disp_index:]
        if DEBUG_CODE: print(f"DEBUG REJECTED: {bytes(tokenized).hex()}")
        if DEBUG_CODE: print(f"DEBUG tokenize_statement: Rejecting statement, returning -1")
        return -1

    index = run_bnf_for_command(tokenized, command_id, lbuff, index)

    # ROM SYNOK: outbuff[stmlbd] = cox -- the displacement from the start of
    # the LINE to just past this statement's last byte.
    #
    # +1 because tokenize_line inserts the line-length byte at offset 2 after
    # every statement is done, shifting everything right by one. Interior
    # statements then have their trailing cCR popped (the cEOS already emitted
    # by f_EOS is what separates statements), which cancels this out -- see the
    # decrement in tokenize_line's multi-statement loop.
    tokenized[disp_index] = len(tokenized) + 1
    global LAST_DISP_INDEX
    LAST_DISP_INDEX = disp_index

    return index


def tokenize_line(lbuff:bytes):
    """
    Tokenize a complete input line of AtariBASIC.

    Parameters
    ----------
    lbuff : bytes
        The raw text of the BASIC line to be tokenized.

    Returns
    -------
    A tuple with an error number and the tokenized line bytes
    """

    # Reset the tokenized line buffer
    tokenized = bytearray()

    # Init the input and output indexes to zero
    cix, cox = 0, 0

    def dbug(msg): pass

    # Highest cix so far
    maxcix = 0

    # Direct statement?
    direct_flag = 0x00

    # Saved name table output
    svontx, svontc, svvvte = 0, 0, 0

    # Point to the variable name table
    svvntp = 0 # (Offset into) variable_name_table

    # ROM SYNTAX zeroes MAXCIX once per LINE when the line is read. Reset it
    # here, at the top of tokenize_line, or the previous line's high-water mark
    # leaks in and the ERROR line marks the wrong character (it matched in
    # isolation but not in a full-file run, where maxcix only ever grew).
    syntaxer.maxcix = 0

    # ROM @SYN0 (ataribas.asm ~325) saves VNTD into SVVNTP before parsing, and
    # the syntax-error path contracts the VNT back to it (~565). Variables
    # created by a FAILED parse must not survive -- otherwise every variable
    # numbered after a bad line is one too high. In test_edge_cases.LST the
    # rejected lines 10-40 mention A and B, which the reference VNT does not
    # contain at all: it starts at A$.
    svvntp = prog._snapshot()

    # Skip leading blanks
    cix = skip_blanks(lbuff, cix)

    # Remember where the statements begin, for the ERROR line (ROM STMSTRT).
    # Set AFTER the line number is consumed, below.

    # Get the line number
    lineno, cix = get_line_number(lbuff, cix)

    # ROM STMSTRT: the first statement character. @SYN0 does SKBLANKS before
    # storing STMSTRT, so the blank after the line number is NOT part of it --
    # including it left a leading 0x20 in the ERROR line's verbatim copy.
    stmt_start = skip_blanks(lbuff, cix)

    # Handle an invalid line number (ERROR 3)
    if not (0 <= lineno <= 32767):
        print(f"ERROR-   3")
        return 3, tokenized

    # Pack into a little‑endian two‑byte array
    line_num = pack_word(lineno)

    # Init the tokenized line with the line num
    tokenized = line_num
    cox += 2

    # Line Number as an int
    line_val = line_num[0] + line_num[1] * 256

    dbug(f"Stored Line Number {line_val}")

    # The original immediate trick is to use line 32768
    # so AtariBASIC programs are limited to 32768 lines.
    if lineno >= 32768: direct_flag = 0x80

    # NOTE: no line-length placeholder here. In the ROM each statement emits
    # its OWN displacement byte (SYN1/SYNOK), and the first statement's
    # displacement is exactly the byte that lands at offset 2. Appending one
    # here as well would produce a spurious extra byte.

    # Skip following blanks
    cix = skip_blanks(lbuff, cix)

    dbug(f"Skipped blanks")

    # Remember the start of the statement for later processing
    statement_start = cix

    # Is the next character a CR?
    c = lbuff[cix]
    if c == ATEOL:
        # Find the line in the statement table and delete it
        delete_line(line_val)
        return 101, bytearray() # Return a value indicating to continue taking input

    # ============================================================
    # Tokenize statements, looping over ':' for multi-statement lines
    # ============================================================

    # ROM SYNTAX-error path (ataribas.asm ~402-455). When a statement fails to
    # parse, BASIC does NOT reject the line -- it stores the source verbatim:
    #
    #   inbuff[maxcix] |= 0x80    invert the char where the parse stalled
    #   stmlbd = 3; cox = 4       reset to one statement
    #   _setcode(kERROR)          then fall into _XDATA
    #   <copy source to CR>       verbatim, including the inverted char
    #   _setcode(CR)              terminate with $9B, not cCR
    #
    # Layout: [ln_lo, ln_hi, line_len, disp, kERROR, <source...>, $9B]
    def build_error_line():
        # ROM: contract the VNT back to SVVNTP, discarding any variables this
        # failed parse created.
        prog._restore_snapshot(svvntp)
        # NOTE: bytearray(line_num) must COPY. `tokenized = line_num` aliases
        # them earlier, so the partially-built statement bytes are still in
        # there; building on the alias would prepend that garbage.
        err = bytearray(line_num[:2])
        err.append(0)               # line length, patched below
        err.append(0)               # displacement, patched below
        err.append(kERROR)
        src = bytearray(lbuff[stmt_start:])
        # Drop the line terminator; it is re-added as the CR below.
        while src and src[-1] in (0x9B, 0x0A, 0x0D):
            src.pop()
        # Invert the character at maxcix to mark where the parse stalled.
        mark = syntaxer.maxcix - stmt_start
        if 0 <= mark < len(src):
            src[mark] |= 0x80
        err += src
        err.append(0x9B)            # ROM _setcode(CR), and CR EQU $9B
        err[2] = len(err)           # line length (whole line, per SYNOK)
        err[3] = len(err)           # single statement, so disp == line length
        return err

    cix = tokenize_statement(tokenized, lbuff, cix)

    # Check if statement tokenization failed (index=-1 indicates failure).
    # The FIRST statement fails here, before the multi-statement loop; it takes
    # the same ROM SYNTAX path and must also become a kERROR line, or a bad
    # first statement is silently dropped instead of preserved verbatim.
    if cix == -1:
        return 0, build_error_line()

    # Handle additional statements separated by ':' (EOS). Atari BASIC allows
    # several statements per line (e.g. `10 A=1:B=2`). In the source, the ':'
    # is consumed by f_EOS and emitted as the cEOS token (0x14); each statement
    # handler also appends its own cCR (0x16). To separate statements we strip
    # the trailing cCR from every statement EXCEPT the last (which is followed
    # by the line terminator 0x9B), leaving cEOS (0x14) between them.
    while cix < len(lbuff) and lbuff[cix] != 0x9B:
        # The previous statement appended its own cCR (0x16); remove it so the
        # cEOS (0x14) already emitted by f_EOS is what separates the statements.
        if tokenized and tokenized[-1] == cCR:
            tokenized.pop()
            # That cCR was counted in this statement's displacement, so take
            # it back off. (It cancels the +1 for the line-length byte that
            # tokenize_line inserts at the end.)
            if LAST_DISP_INDEX is not None:
                tokenized[LAST_DISP_INDEX] -= 1
        prev_cix = cix
        cix = tokenize_statement(tokenized, lbuff, cix)
        if cix == -1:
            return 0, build_error_line()
        if cix <= prev_cix:
            # The statement consumed NO input. Without this guard the loop
            # spins forever, appending tokens until the displacement byte
            # overflows 255 and bytearray raises ValueError -- which is how
            # `411?AB$` (any multi-character string variable after PRINT)
            # crashed the tokenizer instead of reporting a syntax error.
            return 0, build_error_line()

    # ROM (ataribas.asm ~474), once the statement loop has finished:
    #     outbuff[2] = cox;        ; SET LINE LENGTH INTO STMT
    # Offset 2 holds the LINE total; each statement's own displacement byte
    # follows it. So the layout is
    #     [ln_lo ln_hi] line_len  disp cmd ... disp cmd ...
    # Insert the line length now rather than reserving it up front, because
    # the per-statement displacements are counted from the start of the line
    # and already include it (COX was 3 when the first statement began).
    tokenized.insert(2, len(tokenized) + 1)

    return 0, tokenized


def tokenize_and_apply_line(lbuff:bytes):
    """
    Tokenize a complete input line of AtariBASIC and apply it.
    - Lines with no number are executed right away (as if they were the last line in the program).
    - Lines with only a number cause the line with matching number to be deleted from the program.
    - Lines with a number and one or more statements are added to the program.
    """
    result, tokenized_line = tokenize_line(lbuff)

    if result != 0:
        return result

    # Check if this is a direct statement (lineno >= 32768)
    if tokenized_line[0] >= 128:  # MSB set = direct execution
        return 0

    # Get the line number (little-endian)
    lineno = tokenized_line[0] + tokenized_line[1] * 256

    # Check if this is a delete command (line number only, no statement)
    if len(tokenized_line) == 3:  # Only lineno + length byte, no cCR
        delete_line(lineno)
        return 0

    # Add the tokenized line to prog.st
    global prog
    if prog is None:
        prog = Program()

    # Insert the tokenized line into StatementTable (maintains sorted order)
    prog.insert_statement(tokenized_line)

    return 0

def consolidate_tokenized_program():
    """
    Gather all the program data into a single buffer matching a real Atari program buffer,
    suitable for saving to a BAS file.
    Return the buffer.

    The .BAS file format:
    - 14-byte header with VNT/VVT/ST offsets (little-endian + 0x100)
    - VNT: variable name table (MSB-terminated names, sorted by length desc)
    - VVT: variable value table (8-byte entries per variable)
    - ST: statement table (linked list of statements by line number)

    When saving, the StatementTable's linked list is written as a contiguous block.
    """
    global prog

    # Build header (14 bytes)
    # Offset 0-1: VNT offset (little-endian, +0x100)
    # Offset 2-3: VVT offset (little-endian, +0x100)
    # Offset 4-5: ST offset (little-endian, +0x100)
    # Offset 6-7: LOMEM (low memory pointer, +0x100)
    # Offset 8-9: HIMEM (high memory pointer, +0x100)
    # Offset 10-13: reserved

    header = bytearray(14)

    # Calculate offsets
    vnt_offset = 14  # Header ends at byte 13

    # Build VNT. Each name ends with its LAST character's high bit set (the
    # ROM's _TVAR / SAVE format) -- not a separate $80 byte -- and the table
    # is terminated by a $00 byte, which VNTD points at.
    vnt_data = bytearray()
    for name in prog.vnt:
        raw = bytearray(name.encode('ascii'))
        raw[-1] |= 0x80
        vnt_data.extend(raw)
    vntd_offset = vnt_offset + len(vnt_data)      # the $00 terminator
    vnt_data.append(0x00)

    vvt_offset = vnt_offset + len(vnt_data)

    # Build VVT: 8 bytes per variable, as the ROM lays it out (_TVAR):
    #   [type, varnum, 6 value bytes]
    # type: $00 numeric, $40 array, $80 string (+1 once DIMmed, which only
    # happens at run time). The old writer omitted varnum, put the value one
    # byte early, and typed arrays as numeric.
    vvt_data = bytearray()
    for varnum, entry in enumerate(prog.vvt):
        name = prog.vnt[varnum] if varnum < len(prog.vnt) else ''
        vtype = entry[0] if entry else 0
        if name.endswith('(') and vtype == 0:
            vtype = 0x40
        value = bytes(entry[1])[:6] if entry and len(entry) > 1 and entry[1] else b''
        vvt_data.append(vtype)
        vvt_data.append(varnum & 0xFF)
        vvt_data.extend(value.ljust(6, b'\x00'))

    st_offset = vvt_offset + len(vvt_data)

    # Build statement table (ST) from StatementTable linked list.
    # The in-memory table uses the payload-only model:
    #   [ln_lo, ln_hi, stmt_len, <payload...>]   (payload ends in cCR 0x16)
    # where stmt_len = payload length (bytes after the 3-byte header).
    # On disk Atari uses the De Re Atari Fig 10-1 line format:
    #   [ln_lo, ln_hi, line_off, stmt_off, <payload...>, cCR]
    #   - line_off = total bytes of this line (incl. the 4-byte header + cCR)
    #   - stmt_off = distance from line start to the first ':' EOS (0x14);
    #                for a single-statement line stmt_off == line_off
    #   - there are ONLY these two offset bytes; statements are contiguous
    #     (a multi-statement line has the EOS 0x14 between statements)
    st_data = bytearray()
    for lineno, stmt in prog.statement_table.list_program():
        # The in-memory form IS the on-disk form: tokenize_line now emits the
        # ROM layout directly --
        #     [ln_lo ln_hi] line_len  disp cmd ... disp cmd ...
        # with a displacement byte per statement (ROM SYN1/SYNOK). This used to
        # synthesise line_off/stmt_off here because the tokenizer only produced
        # ONE length byte for the whole line; doing that now would insert a
        # second copy and corrupt every line.
        st_data.extend(stmt)

    if DEBUG_CODE: print(f"DEBUG: Wrote {len(list(prog.statement_table.list_program()))} statements to ST")

    # The program MUST end with line 32768, the immediate-mode line. LIST,
    # RUN and GOTO walk the statement table until they reach a line number
    # >= 32768; without it they run off the end into whatever follows, which
    # is exactly the "won't RUN, LIST freezes" symptom. A real SAVE writes
    # the immediate line that was being executed (the SAVE command itself);
    # emit a minimal one -- END -- unless the table already has it.
    #   [00 80] line_len=6  disp=6  cEND($15)  cCR($16)
    stmcur_offset = st_offset + len(st_data)
    # Walk the line-size chain to find whether the last line is 32768.
    pos, last = 0, None
    while pos + 2 < len(st_data):
        ln = st_data[pos] | (st_data[pos + 1] << 8)
        size = st_data[pos + 2]
        if size == 0:
            break
        last = (pos, ln)
        pos += size
    if last and last[1] >= 32768:
        stmcur_offset = st_offset + last[0]
    else:
        st_data.extend(bytes([0x00, 0x80, 0x06, 0x06, 0x15, 0x16]))

    # Assemble final buffer
    result = bytearray()

    # Header: the seven ROM pointers SAVE writes, as offsets from VNTP
    # biased by +0x100 (VNTP itself is always 0x0100):
    #   0-1 LOMEM (0)   2-3 VNTP   4-5 VNTD (VNT's $00 terminator)
    #   6-7 VVTP        8-9 STMTAB 10-11 STMCUR (the line-32768 line)
    #   12-13 STARP (end of the statement table)
    # VNTD and STMCUR were written as VNTP and 0, which atari800 rejected.
    # load_file_BAS recovers the real offset via: HEADER_SIZE + stored − 0x100
    bias = lambda off: ((off - 14 + 0x100) & 0xFFFF).to_bytes(2, 'little')
    header[0:2]   = bytes([0x00, 0x00])                    # LOMEM
    header[2:4]   = bias(vnt_offset)                       # VNTP  (0x0100)
    header[4:6]   = bias(vntd_offset)                      # VNTD
    header[6:8]   = bias(vvt_offset)                       # VVTP
    header[8:10]  = bias(st_offset)                        # STMTAB
    header[10:12] = bias(stmcur_offset)                    # STMCUR
    header[12:14] = bias(st_offset + len(st_data))         # STARP

    result.extend(header)
    result.extend(vnt_data)
    result.extend(vvt_data)
    result.extend(st_data)

    return bytes(result)

def save_program(outpath, asListing=False, abbrev=args_abbrev):
    """Save the stored program to a tokenized .BAS file or to a .LST file."""
    if asListing:
        # Write LST (text listing)
        try:
            f = open(outpath, 'w')
        except IOError as e:
            print(f"Error {e} trying to open {outpath}")
            sys.exit(1)

        oldout = sys.stdout
        sys.stdout = f
        list_program_from_st(abbrev=abbrev)
        sys.stdout = oldout
        f.close()
    else:
        # Write BAS (tokenized binary)
        buffer = consolidate_tokenized_program()
        try:
            f = open(outpath, 'wb')
            f.write(buffer)
            f.close()
            print(f"Saved to {outpath}")
        except IOError as e:
            print(f"Error {e} writing to {outpath}")
            sys.exit(1)

# --------------------------------------------------------------------------- #
# Program Runner
# --------------------------------------------------------------------------- #

def execute_tokenized_statement(st_bytes):
    """
    Execute a single tokenized statement.
    """
    pass

def execute_tokenized_line(line_bytes):
    """
    Execute a whole tokenized line.
    """
    pass

# --------------------------------------------------------------------------- #
# Interactive Mode
# --------------------------------------------------------------------------- #

def interactive_mode():
    """
    Enhanced interactive mode with READY prompt and hex dump output:
    - Accept a line of input, parse it as AtariBASIC.
    - Display hex dump of tokenized output for debugging.
    - Handle errors gracefully with clear messages.
    """
    nowReady = True
    print("=" * 60)
    print("ATARI BASIC TOKENIZER - Interactive Mode")
    print("=" * 60)
    print("Type a BASIC statement to tokenize, or use commands:")
    print("  LIST - List program")
    print("  CLIST - Colorized list (if available)")
    print("  SAVE <file> - Save program to .BAS file")
    print("  QUIT, EXIT, Q - Exit")
    print("=" * 60)

    while True:
        if nowReady:
            print("\nREADY")
            nowReady = False

        try:
            line_str = input("> ") # String
        except (KeyboardInterrupt, EOFError) as exc:
            if isinstance(exc, KeyboardInterrupt):
                # Ctrl‑C is the BREAK key
                print(" BREAK")
                continue
            else:
                # Ctrl‑D quits from interactive mode
                print()
                break

        cmd = line_str.strip().upper()
        if cmd in ("QUIT", "EXIT", "Q"):
            print("\nBye!\n")
            break

        # Run simple recognized commands without tokenizing
        import re
        m = re.match(r'([A-Z]+|[.\?])(.*)', cmd)
        if m:
            cmd_name = m[1]
            handler = COMMAND_HANDLERS.get(cmd_name)
            if handler:
                if cmd_name in ["LIST", "CLIST"]:
                    args = m[2].strip()
                    handler(args)
                else:
                    handler()
                nowReady = True
                continue

        if line_str != "":
            # Let's just dive into tokenizing the line
            # Convert from UTF-8 bytes to ATASCII bytes and append ATEOL
            line_bytes = unicode_to_atascii_str(bytes(line_str + "\n", "utf-8"))

            # Use tokenize_and_apply_line to properly store in StatementTable
            error_code = tokenize_and_apply_line(line_bytes)

            if error_code != 0:
                print(f"ERROR {error_code}: Invalid statement")
            else:
                # Show hex dump of tokenized output for debugging
                global prog
                if prog and len(prog.statement_table.data) > 0:
                    # Find the last statement added from StatementTable
                    stmt_tokens = None
                    for lineno, stmt in prog.statement_table.list_program():
                        # stmt is already a complete statement [ln_lo, ln_hi, len, ...]
                        if len(stmt) > 3:
                            stmt_tokens = stmt
                            break

                    if stmt_tokens and len(stmt_tokens) > 3:
                        print(f"Tokenized ({len(stmt_tokens)} bytes):")
                        hex_str = " ".join(f"{b:02X}" for b in stmt_tokens)
                        print(f"  {hex_str}")

                        # Show command token name if known
                        cmd_token = stmt_tokens[3]  # Command is at offset 3
                        from ataridefs import ops_and_funcs, commands_info

                        cmd_name = "UNKNOWN"
                        if cmd_token < len(commands_info):
                            cmd_name = commands_info[cmd_token].get('name', 'UNKNOWN')
                        elif cmd_token < len(ops_and_funcs):
                            cmd_name = ops_and_funcs[cmd_token] or "UNKNOWN"

                        print(f"  Command: {cmd_name} (token={cmd_token})")

                        # Check for cCR marker
                        if len(stmt_tokens) > 1 and stmt_tokens[-1] == 22:
                            print(f"  ✓ End-of-line marker present")
                        else:
                            print(f"  ✗ Missing end-of-line marker (last byte={stmt_tokens[-1] if stmt_tokens else 'empty'})")

                nowReady = True

# --------------------------------------------------------------------------- #
# LOAD from BAS file
# --------------------------------------------------------------------------- #


def load_file_BAS(inpath):
    """
    Load the complete program from a BAS file into our data structures.
    Return True if successful.
    """
    try:
        with open(inpath, "rb") as f:
            buffer = bytearray(f.read())
    except FileNotFoundError:
        print(f"Error: File not found – {inpath}")
        return False
    except IOError as e:
        print(f"Error reading {inpath}: {e}")
        return False

    # ----- BASIC header validation -----
    HEADER_SIZE = 14

    # First two bytes should be little‑endian 0 (0x0000)
    # Next two bytes should be little‑endian 256 (0x0100)
    if len(buffer) < 4 or buffer[0:2] != b'\x00\x00' or buffer[2:4] != b'\x00\x01':
        print("Error: Not a valid Atari BASIC file.") ; return False

    # Bytes 4-5 are UNUSED

    # Bytes 6-7 are the VVT offset, plus 0x100
    vvt_offset = HEADER_SIZE + getint(buffer[6:8]) - 0x100

    # Bytes 8-9 are the Statement Table offset, plus 0x100
    st_offset = HEADER_SIZE + getint(buffer[8:10]) - 0x100

    # Bytes 10-11 are UNUSED

    # Bytes 12-13 are the offset to end of file from the beginning of the data, plus 0x100
    end_offset = HEADER_SIZE + getint(buffer[12:14]) - 0x100

    # Load the raw Variable Name Table data into the variable_name_table array
    vnt_data = buffer[HEADER_SIZE:vvt_offset]
    prog.ingest_variable_name_table(vnt_data)

    # Load the raw Variable Value Table data into the variable_value_table array
    vvt_data = buffer[vvt_offset:st_offset]
    prog.ingest_variable_value_table(vvt_data)

    # Load the raw Statement Table data into the statement_table array
    st_data = buffer[st_offset:end_offset]

    # Our tokenizer outputs ROM token values directly (no renumbering).
    # The rom_tokens conversion that existed here was removed — restore it
    # only if you need to load .BAS files that use a different token space.

    # Apply conversion to statement table data.
    # On-disk Atari ST line format (De Re Atari Fig 10-1):
    #   [ln_lo, ln_hi, line_off, stmt_off, <statements...>, cCR]
    #   - line_off  = bytes from line start to next line start (== total line size)
    #   - stmt_off  = bytes from line start to the first ':' EOS (0x14) of
    #                the line; for a single-statement line stmt_off == line_off
    #   - there are ONLY these two offset bytes; statements are laid out
    #     contiguously (no per-statement trailing offset bytes)
    # We convert ROM command tokens -> renumbered tokens AND rebuild the table
    # into the in-memory payload-only model the StatementTable logic uses:
    #   [ln_lo, ln_hi, stmt_len, <payload...>]
    # where stmt_len = payload length (bytes after the 3-byte header).
    # A multi-statement line splits into several in-memory statements at each
    # EOS (0x14) separator.
    # The on-disk ST layout and the in-memory model are now IDENTICAL:
    #     [ln_lo ln_hi] line_len  disp cmd ... disp cmd ...
    # tokenize_line emits the ROM's per-statement displacement bytes directly
    # (SYN1/SYNOK), so no rewriting is needed. This previously split each line
    # into one in-memory record per statement, re-headed with a payload length;
    # doing that now would shift every statement by one byte and make the
    # detokenizer read a displacement where a command token belongs.
    prog.ingest_statement_table(st_data)

    #print(f"Offsets: VNT={HEADER_SIZE}, VVT={vvt_offset}, ST={st_offset}, END={end_offset}")
    #print(f"Lengths: VNT={len(vnt_data)}, VVT={len(vvt_data)}, ST={len(st_data)}")

    return True

# --------------------------------------------------------------------------- #
# LOAD from LST file
# --------------------------------------------------------------------------- #

def load_file_LST(inpath, outfile=None):
    """
    Load a complete input file of AtariBASIC in LST format, tokenize_and_apply_line each line.
    This function expects the input in ATASCII native format,
    but later we can use atascii.py (i.e., import atascii) to
    convert Atari-specific UTF-8 characters to ATASCII.

    Handles both:
    - ATASCII LST files (HardDrive1): $9B newline separator, raw binary
    - Unicode LST files: standard LF ($0A) line endings

    If outfile is provided, saves the tokenized program as a .BAS file.
    """
    global prog, syntaxer

    # Per-line tokenize failures, for front-ends that cannot see stdout.
    LOAD_ERRORS.clear()

    # Read the LST file
    try:
        with open(inpath, 'rb') as f:
            raw_data = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {inpath}")
        return

    # Detect format and split into lines.
    #
    # Do NOT test `b'\x9B' in raw_data`: 0x9B is a legal UTF-8 CONTINUATION
    # byte, and the PUA codepoints a .ULST uses encode with it (U+E0DB is
    # ee 83 9b). Three of the dev samples were misdetected as ATASCII that
    # way, split on a byte in the middle of a character, and failed to
    # tokenize -- which is what left the Inspector's panes empty.
    #
    # A real ATASCII listing is not valid UTF-8 (it is raw high-bit bytes), so
    # decode first and only fall back to the 0x9B split when that fails.
    try:
        raw_data.decode('utf-8')
        is_unicode = True
    except UnicodeDecodeError:
        is_unicode = False

    if not is_unicode and b'\x9B' in raw_data:
        # ATASCII LST format (HardDrive1) - $9B as newline separator
        lines = raw_data.split(b'\x9B')
        if lines and lines[-1] == b'':
            lines = lines[:-1]  # Remove trailing empty element
        newline_type = 'atascii'
    else:
        # Unicode LST format - standard LF ($0A) line endings.
        # The file is UTF-8 where Atari-specific glyphs are PUA codepoints
        # (U+E000+), so convert to native ATASCII before tokenizing -- the
        # tokenizer works in ATASCII bytes and would otherwise see raw UTF-8
        # multi-byte sequences and reject the line with ERROR 3. Split FIRST
        # (on the UTF-8 LF, which is unambiguous) and convert per line, because
        # ATASCII 0x0A is a legal data byte in PUA form and converting the whole
        # file first would manufacture false line breaks.
        lines = raw_data.split(b'\n')
        lines = [unicode_to_atascii_str(ln) for ln in lines]
        newline_type = 'unicode'

    # Initialize program structure
    prog = Program()
    syntaxer.program = prog  # Bind the syntaxer to the new program so VNT names are recorded on it

    # Tokenize each line
    for line_bytes in lines:
        if not line_bytes.strip():
            continue

        # NOTE: do not strip $00/$01 here. They are ordinary ATASCII
        # characters (heart, left-tee) and appear inside string literals.

        # Skip host comment lines. ';' and '#' are the modern markers; '.'
        # appears in legacy ULST files, where an unnumbered line was a bare
        # REM (real Atari BASIC parses those as line 32768 and discards them).
        # None can start a real program line, which must begin with a digit.
        # Matches atascii.COMMENT_MARKERS, used by the ULST->LST converter.
        stripped = line_bytes.lstrip()
        if stripped and stripped[0:1] in COMMENT_MARKERS:
            continue

        # Tokenize the line
        error_code, tokenized = tokenize_line(line_bytes)

        if error_code != 0:
            print(f"ERROR {error_code} on line: {line_bytes.decode('utf-8', errors='replace')}")
            # Record it too. Printing alone loses the failure in --json mode,
            # where stdout is redirected to a sink -- the Inspector then showed
            # an empty listing with error:null and no hint anything went wrong.
            LOAD_ERRORS.append({
                'error': error_code,
                'line': line_bytes.decode('utf-8', errors='replace'),
            })
            continue

        # Insert into program structure
        prog.insert_statement(tokenized)

    print(f"Loaded {len(list(prog.statement_table.list_program()))} statements from {inpath}")

    # If outfile is provided, save the tokenized program
    if outfile:
        buffer = consolidate_tokenized_program()
        try:
            f = open(outfile, 'wb')
            f.write(buffer)
            f.close()
            print(f"Saved to {outfile}")
        except IOError as e:
            print(f"Error writing to {outfile}: {e}")

def emit_json():
    """Emit the whole program as JSON for an editor front-end (VSCode WebView).

    One invocation gives every pane the UI needs:
      listing      - normal LIST output
      listing_abbr - compact/abbreviated LIST output
      hex          - the on-disk .BAS image, per-line hex plus the 14-byte header
      vnt          - variable names
      vvt          - variable types

    Capturing list_program_from_st()'s stdout keeps ONE listing implementation;
    reimplementing it here would drift from the tokenizer.
    """
    import io
    import json as _json
    from contextlib import redirect_stdout

    def capture(fn, *a, **k):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(*a, **k)
        return buf.getvalue().rstrip('\n')

    out = {
        'listing': capture(list_program_from_st, abbrev=False),
        'listing_abbr': capture(list_program_from_st, abbrev=True),
        'vnt': [],
        'vvt': [],
        'lines': [],
        'header': None,
        'error': None,
    }

    # Variables: prefer the BAS-side model, fall back to the tokenizer's.
    vvt = getattr(prog, 'variable_value_table', None)
    if vvt:
        for i, v in enumerate(vvt):
            out['vnt'].append(prog.variable_name(i))
            out['vvt'].append({
                'index': i, 'token': 0x80 | i, 'type': v.get('type', 0),
                'disp': v.get('disp', -1), 'dim1': v.get('dim1'),
                'dim2': v.get('dim2'), 'maxl': v.get('maxl'),
                'curl': v.get('curl'), 'value': v.get('value'),
            })
    else:
        names = getattr(prog, 'vnt', []) or []
        types = getattr(prog, 'vvt', []) or []
        for i, name in enumerate(names):
            entry = types[i] if i < len(types) else None
            out['vnt'].append(name)
            out['vvt'].append({
                'index': i, 'token': 0x80 | i,
                'type': entry[0] if entry else 0,
                'declared_only': entry is None,
            })

    # The on-disk image, split per statement-table line so the hex pane can
    # align with the listing.
    try:
        image = bytes(consolidate_tokenized_program())
        out['header'] = image[:14].hex()
        w = lambda o: image[o] | (image[o + 1] << 8)
        st, end = 14 + w(8) - 0x100, 14 + w(12) - 0x100
        body, i = image[st:end], 0
        while i < len(body) - 2:
            ln = body[i] | (body[i + 1] << 8)
            ll = body[i + 2]
            if ll == 0:
                break
            out['lines'].append({'line': ln, 'hex': body[i:i + ll].hex()})
            i += ll
    except Exception as e:                       # noqa: BLE001
        out['error'] = f'{type(e).__name__}: {e}'

    # Per-line tokenize failures. A file can produce NO statements and still
    # raise nothing, so without this the Inspector rendered a blank listing
    # with error:null. Report the count and the first few offenders.
    if LOAD_ERRORS:
        out['line_errors'] = LOAD_ERRORS[:20]
        if out['error'] is None:
            n = len(LOAD_ERRORS)
            out['error'] = (f'{n} line{"" if n == 1 else "s"} failed to '
                            f'tokenize; see line_errors')

    print(_json.dumps(out, indent=2))


def main():
    # Requires Python 3
    if sys.version_info[0] < 3:
        print("This script requires Python 3")
        sys.exit(1)

    """
    Parse input arguments and decide where to route the program next.
    - If there is an 'infile' argument tokenize all the file's lines.
    - For an 'outfile' argument:
      - The argument is invalid without an 'infile' argument.
      - Save the tokenized BASIC to the given filename.
      - Exit.
    - For no 'outfile' argument:
      - Enter Interactive Mode.
      - (Once the Program Runner is written we will run the program instead.)
    """
    parser = argparse.ArgumentParser(
        description="AtariBASIC Playground",
        usage="basic.py [-l|--list] [-o|--output <outfile>] [infile]"
    )
    parser.add_argument('-l', '--list', action="store_true", help='Just list the program and exit.')
    parser.add_argument('-c', '--clist', action="store_true", help='List the colorized program and exit.')
    parser.add_argument('-a', '--abbrev', action="store_true", help='Produce an abbreviated listing.')
    parser.add_argument('-s', '--struct', action="store_true", help='Display structured output.')
    parser.add_argument('-t', '--tvars', action="store_true", help='Print the VNT and VVT tables.')
    parser.add_argument('-j', '--json', action="store_true",
                        help='Emit listing, hex, VNT and VVT as JSON on stdout (for editor front-ends).')
    parser.add_argument('-o', '--output', type=Path, help='Path to write the resulting tokenized program (BAS file).')
    parser.add_argument('infile', nargs='?', type=Path, help='Optional input file (LST or BAS).')
    args = parser.parse_args()

    # ----- Argument validation -----
    if not args.infile:
        if args.output: parser.error("The '-o/--output' option requires an input file.")
        if args.list: parser.error("The '-l/--list' option requires an input file.")
        if args.clist: parser.error("The '-c/--clist' option requires an input file.")
        if args.tvars: parser.error("The '-t/--tvars' option requires an input file.")

    # Display listings with abbreviated keywords, minimal whitespace
    global args_abbrev
    if args.abbrev: args_abbrev = True

    # Display listings in a structured format
    global args_struct
    if args.struct: args_struct = True

    # Display listings in color
    global args_colorify
    if args.clist:
        args.list = True
        args_colorify = True

    if not args.list and not args.json:
        color_print("AtariBASIC Playground\n(c) 2025 Thinkyhead", color=token_color['comment'])

    # ----- Handle the three modes -----
    if args.infile:
        # Detect file type by extension or content
        infile_str = str(args.infile).lower()

        # In JSON mode stdout must contain NOTHING but the JSON document, so
        # swallow the loader's progress chatter ("Loaded N statements...").
        import io as _io
        from contextlib import redirect_stdout as _redirect, nullcontext as _nullctx
        _sink = _io.StringIO()
        _quiet = _redirect(_sink) if args.json else _nullctx()

        with _quiet:
            # .LST is an ATASCII listing; .ULST is the same thing in Unicode
            # (the user's convention for telling the two apart). Both are
            # source listings to tokenize -- anything else is a tokenized BAS.
            # NOTE: infile_str is already lowercased above, so testing '.LST'
            # here would be dead code.
            if infile_str.endswith(('.lst', '.ulst')):
                # LST/ULST file - tokenize and optionally save as BAS
                load_file_LST(args.infile, args.output)
            else:
                # BAS file - load tokenized program
                result = load_file_BAS(args.infile)
                if result is False: sys.exit(1)

        if args.json:
            emit_json()
            exit(0)

        # If an output file was supplied, write the buffer there.
        if args.output != None:
            save_program(args.output, args.list)
            exit(0)
        elif args.list:
            if args.tvars: prog.print_variable_tables()
            list_program_from_st(abbrev=args_abbrev)
            exit(0)
        elif args.tvars:
            prog.print_variable_tables()
            exit(0)

    interactive_mode()

if __name__ == "__main__":
    main()
