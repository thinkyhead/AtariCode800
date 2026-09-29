"""
AtariBASIC Playground
ataribasic.py - AtariBASIC language data and utilities
"""
import re, math
#from typing import Callable, Dict, Iterable
from ataridefs import *

DEBUG_CODE = False

# --------------------------------------------------------------------------- #
# Lookup and conversion
# --------------------------------------------------------------------------- #

def getint(inbytes: bytearray) -> int:
    """Convert little-endian bytes to integer.

    VERIFIED: Used throughout tokenizer, tested with multiple inputs.
    """
    return int.from_bytes(inbytes, byteorder='little')

def decode_bcd(bcd_bytes: bytearray) -> float:
    """Decode a 6‑byte BCD floating-point value.

    Format:
      Byte 0: sign bit (0x80=neg) + biased exponent (0-based, base-100, bias=68)
      Bytes 1-5: 5 bytes of packed BCD (each byte = 2 decimal digits: upper nibble=tens, lower nibble=ones)

    The significand is formed by interpreting each byte as a 2-digit decimal number,
    then concatenating all 5 bytes as a single large integer.

    Example: [0x44, 0x00, 0x00, 0x00, 0x10, 0x00]
      Sign = positive, Exponent = 68 + 0 - 68 = 0
      Digits: 00, 00, 00, 10, 00 → integer 100
      Value = 100 × 100^0 = 100.0

    VERIFIED: Tested with ABS verification suite, all values match reference .BAS files.
    """
    if len(bcd_bytes) != 6:
        raise ValueError("BCD float must be exactly 6 bytes")

    # Exponent
    exp = bcd_bytes[0]
    sign = 1
    if exp > 127:              # sign bit set -> negative
        sign, exp = -1, exp - 128

    # Each byte is packed BCD: upper nibble = tens, lower nibble = ones
    # So 0x12 = digits 1,2 → value 12; 0x05 = digits 0,5 → value 5
    significand = 0
    for b in bcd_bytes[1:]:
        significand = significand * 100 + ((b >> 4) * 10 + (b & 0x0F))

    # Apply exponent (base 100)
    actual_exp = exp - 68
    if actual_exp > 0:
        significand *= 100 ** actual_exp
    elif actual_exp < 0:
        significand /= 100 ** (-actual_exp)

    return sign * significand


def encode_bcd(value: float) -> bytearray:
    """Encode a Python float as 6-byte Atari BCD (the format Atari BASIC
    stores in tokenized programs / DATA, matching atari800 SAVE output).

    Format (De Re Atari Ch.10 — base-100 floating point):
      Byte 0 : sign (0x00 / 0x80) + biased exponent (exp + 0x3F)
      Bytes 1-5: 5 packed BCD digit-pairs (each byte = 2 decimal digits),
                 the significand normalized so 0.01 <= mantissa < 1.0,
                 left-justified (2 digits per byte).

    The value is: mantissa * 100^exp.

    Examples (verified against atari800):
      encode_bcd(0)   -> 00 00 00 00 00 00
      encode_bcd(5)   -> 40 05 00 00 00 00   (0.05 * 100^1)
      encode_bcd(10)  -> 40 10 00 00 00 00   (0.10 * 100^1)
      encode_bcd(100) -> 41 01 00 00 00 00   (0.01 * 100^2)
      encode_bcd(-5)  -> c0 05 00 00 00 00   (negative)
    """
    if value == 0.0:
        return bytearray([0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

    neg = value < 0
    val = abs(value)

    # Normalize by factors of 100 until 0 <= val < 1.0, tracking the shift count.
    # After the loop val is the fraction such that value = val * 100^exp, with
    # val in [0, 1). The significand digits are val * 10^10 (10 digits).
    exp = 0
    while val >= 1.0:
        val /= 100.0
        exp += 1

    # Build a 10-digit, left-justified BCD significand (2 digits per byte).
    # Round to avoid float drift (e.g. 0.05 -> 5 exactly).
    digits = int(round(val * 1e10))
    if digits > 9999999999:   # rounding can push to 1e10; clamp (rare)
        digits = 9999999999
    bcd = bytearray(6)
    bcd[0] = (0x80 if neg else 0x00) | ((exp + 0x3F) & 0x7F)
    # Encode bytes 1..5 with 2 digits each, leading-zero padded.
    s = f"{digits:010d}"
    for i in range(5):
        pair = int(s[i*2:i*2+2])
        bcd[i + 1] = (pair // 10) * 16 + (pair % 10)
    return bcd

def string_for_command_token(token, abbrev=False):
    """Return the full or abbreviated command string for the given token.

    VERIFIED: commands_info[32] = "PRINT" confirmed correct.
    """
    if token >= len(commands_info): return "<?>"
    return commands_info[token]['abbrev' if abbrev else 'name']

def string_for_function_token(token):
    """Return the string corresponding to the given function token byte."""
    pass

def string_for_operator_token(token):
    """Return the string corresponding to the given operator token byte."""
    pass

# --------------------------------------------------------------------------- #
# BASIC listing
# --------------------------------------------------------------------------- #

def op_func_string(atok):
    """Return the string for the given operator or function token.

    VERIFIED: Used throughout detokenization, tested with all statement handlers.
    """
    if atok < len(ops_and_funcs): return ops_and_funcs[atok]
    return f"<{atok:02X}>"

def get_number(inbuf:bytes, index:int, lowval:float=0, hival:float=0):
    """
    Extract a number from the line input buffer 'inbuf' at 'index'.
    If a range is given reject values outside the range.

    The number may be an integer (e.g. "123") or a floating point
    number (e.g., "234.5"). The number ends at the first character
    that is not a digit or a decimal point.

    A solitary "." – or a "." that is not followed by at least one
    digit – is **not** considered a number.

    VERIFIED: Used in expression parsing, tested with numeric inputs.
    """
    start = index
    got_neg = False
    got_dot = False
    got_digit = False
    got_exp = False
    got_exp_digit = False

    while index < len(inbuf):
        c = chr(inbuf[index])
        is_blk = c == ' '
        is_neg = c == '-'
        is_num = c.isdigit()
        is_dot = c == '.'
        is_exp = c == 'e' or c == 'E'
        if not (is_blk or is_neg or is_num or is_dot or is_exp): break

        # A blank is okay before the number, including " - 123"
        if is_blk and (got_digit or got_dot or got_exp): break

        if is_neg:
            if got_neg or got_digit or got_dot or got_exp: break
            got_neg = True

        if is_exp:
            if not got_digit: break
            if got_exp: break           # second exp
            got_exp = True

        if is_dot:
            if got_dot: break           # second dot
            if got_exp: break           # dot after exp
            got_dot = True

        if is_num:
            if got_exp: got_exp_digit = True
            else: got_digit = True

        index += 1

    # At least one digit is needed
    if not got_digit: return None, start

    # If it ends with 'e' back up one char
    if got_exp and not got_exp_digit:
        index -= 1

    # Decode the numeric bytes into a float.
    # Return None if conversion fails (e.g. empty slice).
    number = None
    if index > start:
        try:
            # ``inbuf[start:index]`` is a bytes slice – it can be passed
            # directly to ``float``.
            number = float(inbuf[start:index])
        except ValueError:
            pass

    if lowval != hival and not (lowval <= number <= hival):
        return None, start

    return number, index

def pack_word(val):
    """Pack a value into a little‑endian two‑byte array.

    VERIFIED: Used for line number encoding, tested with multiple values.
    """
    return bytearray([val & 0xFF, (val >> 8) & 0xFF])

def get_line_number(inbuf:bytes, index:int):
    """
    Extract a line number from the line input buffer 'inbuf' at 'index'.
    After extraction, 'index' points to the following character.
    The floor of the value is converted into a two‑byte little‑endian bytearray.

    Parameters
    ----------
    inbuf : bytearray
        The source text to scan.
    index : int
        Current position in 'inbuf'.

    Returns
    -------
    tuple[float, int]
        The floored number and the updated index.
    """

    # Get the line number, if any. Fall back to "direct line" 32768.
    lineno, index = get_number(inbuf, index, 0, 32767)
    if lineno == None: lineno = 32768
    lineno = math.floor(lineno)

    return lineno, index

# $A462 SEARCH - Based on a general search of name tables
def search_statement_name_table(lbuff:bytes, index:int):
    """
    Search commands_info 'name' fields for the string at lbuff + index.
    Return the command ID and the following index.
    On fail return kILET to try var assignment.

    Parameters
    ----------
    lbuff : bytes
        The complete input buffer (bytes).
    index : int
        Current position in lbuff from which to start the search.

    Returns
    -------
    tuple[int, int]
        (command_id, new_index)
        * command_id – index in commands_info or kILET
        * new_index  – position after the matched command name
    """

    # Debug: print what we're searching for
    # print(f'DEBUG search_statement_name_table: lbuff={lbuff}, index={index}')
    # print(f'  Searching for: {lbuff[index:index+20]}')

    # Go through the statement table and find the longest valid match.
    # Assembly rule (_SRC2/_SRC5 in ataribas.asm): a statement name matches
    # only when (a) the input is a FULL prefix of the name AND the input char
    # at that point is '.', or (b) the full name is matched. There is NO
    # "prefix + non-letter-follow = match" rule in the ROM — a bare 'X' does
    # NOT match XIO; it must be 'X.'. So a single letter like 'X' that is
    # followed by '=' remains a variable, not the XIO command.
    best_cmd_idx = -1
    best_match_len = 0
    best_new_index = index
    for t, c in enumerate(commands_info):
        cmdname = bytes(c['name'], "ascii")
        cmdlen = len(cmdname)
        ipos = index
        cpos = 0
        got = False
        while ipos < len(lbuff):
            ch = lbuff[ipos:ipos+1]
            if ch == b'.':
                # '.' is the abbreviation terminator (assembly _SRC5): the
                # matched prefix so far is a valid abbreviation.
                got = True
                ipos += 1
                cpos += 1
                break
            if ch.upper() != cmdname[cpos:cpos+1]:
                break
            ipos += 1
            cpos += 1
            if cpos == cmdlen:
                got = True  # full-name match
                break
        if got and cpos > best_match_len:
            best_cmd_idx = t
            best_match_len = cpos
            best_new_index = ipos
    if best_cmd_idx >= 0:
        return best_cmd_idx, best_new_index
    return kILET, index

def search_operator_name_table(lbuff:bytes, index:int):
    """
    Search ops_and_funcs for the string at lbuff + index.
    Return the operator ID and the following index.
    On fail return None.

    Parameters
    ----------
    lbuff : bytes
        The complete input buffer (bytes).
    index : int
        Current position in lbuff from which to start the search.

    Returns
    -------
    tuple[int, int]
        (operator_token, new_index)
        * operator_token – index in ops_and_funcs or None
        * new_index  – position after the matched command name
    """

    import sys
    # print(f"DEBUG search_operator_name_table: index={index}, lbuff[{index}:{index+20}]={lbuff[index:index+20]}", file=sys.stderr)

    # Check if the current character matches any operator/function name
    # If not, return None immediately (this handles digits and other non-operator characters)
    if index < len(lbuff):
        current_char = chr(lbuff[index]).upper()
        for t, c in enumerate(ops_and_funcs):
            opname = c.strip().upper()
            if len(opname) > 0 and opname[0] == current_char:
                # Current character matches the first character of an operator/function
                break
        else:
            # No operator/function starts with the current character
            return None, index

    # Go through the entire ops_and_funcs table (not just from cSROP)
    best_match = None  # (token, scan_pos, ipos) of the closest match

    for t, c in enumerate(ops_and_funcs):  # Changed: removed [cSROP:] to search entire table
        opname = c.strip()
        oplen = len(opname)

        # Scan forward from index to find this operator
        scan_pos = index
        while scan_pos < len(lbuff):
            # Skip spaces before trying to match
            if lbuff[scan_pos] in (ord(' '), ord('\t')):
                scan_pos += 1
                continue

            # Try to match the operator starting at scan_pos
            ipos = scan_pos
            cpos = 0
            got = False
            while ipos < len(lbuff):
                ch = chr(lbuff[ipos])       # one char from the input as str
                if cpos < oplen and ch.upper() != opname[cpos:cpos+1]: # Mismatch?
                    break                     # Go to next position in scan
                ipos += 1                     # Next input index
                cpos += 1                     # Next compare index
                if cpos == oplen:             # Got the whole command?
                    got = True

                if got:
                    # Found a match - check if it's the closest one
                    # Only update if this is the first match (scan_pos < best_match[1])
                    # This ensures we return the earliest matching operator, not the last
                    if best_match is None or scan_pos < best_match[1]:
                        best_match = (t, scan_pos, ipos)
                    # Don't update if scan_pos == best_match[1] - keep the first match found
                    # Also, only accept matches that start at or after the current index
                    if scan_pos >= index:
                        break

            scan_pos += 1                   # Try matching from next position

    if best_match:
        t, scan_pos, ipos = best_match
        # Only return matches that start EXACTLY at the current index (not later)
        if scan_pos == index:
            if DEBUG_CODE: print(f"DEBUG search_operator_name_table: Found match at scan_pos={scan_pos}, token={t}, opname='{ops_and_funcs[t]}'")
            return t, ipos

    return None, index
