"""
StatementTable - Atari BASIC Statement Table Management

This module implements the Atari BASIC statement table as a linked list,
matching the structure used in the original ROM.

The StatementTable class IS the tokenized BASIC program - it represents
the complete in-memory representation including all statements, sorted by
line number with proper expansion/contraction support.

Usage:
    from statement_table import StatementTable

    st = StatementTable()
    st.insert_statement(lineno, tokenized_line)  # Auto-sorts by line number
    st.delete_statement(lineno)                   # Remove a line

    for lineno, stmt in st.list_program():
        print(f"{lineno}: {stmt}")

When saving to .BAS file, the linked list is written as a contiguous block.
"""

from typing import Optional, Tuple, Iterator


class StatementTable:
    """
    Atari BASIC statement table implemented as a sorted linked list.

    This class manages the tokenized program data, maintaining statements
    in line number order and handling memory expansion/contraction.

    The statement table format matches the Atari BASIC ROM:
    - Each line starts with 2-byte little-endian line number
    - Followed by 1-byte statement length (includes header bytes)
    - Then N bytes of tokenized statement data
    - End marker: line number 0

    Example:
        [10, 0, 5, 32, 22]   # Line 10: PRINT "HELLO" (length=5)
        [20, 0, 6, 34, 81, 79, 76, 76, 22]  # Line 20: DATA "YOLLO" (length=6)
        [0, 0]                # End marker
    """

    def __init__(self):
        """Initialize empty statement table."""
        self.data = bytearray()  # Raw statement table data (linked list)

    def line_size(self, pos: int) -> int:
        """Total bytes of the line starting at `pos`, header included.

        Byte 2 of a line is the ROM's line-length byte, which counts the WHOLE
        line -- the 2-byte line number and the length byte itself included
        (ataribas.asm ~474: `outbuff[2] = cox`, and COX is 3 when the first
        statement begins). It is NOT a payload length, so do not add 3 to it.

        Returns 0 for a malformed/zero length so callers can break out instead
        of looping forever.
        """
        if pos + 2 >= len(self.data):
            return 0
        return self.data[pos + 2]

    # Line 32768 ($8000) is the ROM's end-of-program marker -- the immediate
    # mode line, which always sorts last because a typed line number cannot
    # exceed 32767. Line 0 is an ORDINARY, LEGAL line number: `0 CLR:LAST=10`
    # is valid Atari BASIC, and the `0 SAVE"..."` / `0 LIST"..."` idiom is how
    # a program is dumped from the emulator. Treating 0,0 as the terminator
    # truncated the table at the first such line -- UNTOKEN.ULST lost 245 of
    # its 257 lines, and the survivors decoded as garbage line numbers.
    END_MARKER = 32768

    def _is_end(self, pos: int) -> bool:
        """True if pos holds the end-of-program marker (line 32768)."""
        if pos + 1 >= len(self.data):
            return True
        return self.data[pos] + (self.data[pos + 1] << 8) >= self.END_MARKER

    def _find_end_marker(self) -> int:
        """Find the end marker (line 32768) in the table."""
        pos = 0
        while pos + 1 < len(self.data):
            if self._is_end(pos):
                return pos
            size = self.line_size(pos)
            if size == 0:
                pos += 3  # Skip past malformed header
                continue
            pos += size
        return len(self.data)

    def find_insertion_point(self, lineno: int) -> Tuple[int, bool]:
        """
        Find where to insert a new line in the sorted linked list.

        Args:
            lineno: Line number to insert (0-32767)

        Returns:
            Tuple of (position, found_exact_match):
            - position: Byte offset where line should be inserted/updated
            - found_exact_match: True if line already exists (for replacement)
        """
        pos = 0

        while pos + 1 < len(self.data):
            # Check for the end marker (line 32768, NOT line 0 -- see _is_end)
            if self._is_end(pos):
                return (pos, False)

            # Compare line numbers (little-endian)
            current_lineno = self.data[pos] + (self.data[pos + 1] << 8)

            if current_lineno > lineno:
                return (pos, False)  # Insert before this line

            if current_lineno == lineno:
                return (pos, True)   # Replace existing line

            # Advance to next statement using the line-length byte
            size = self.line_size(pos)

            # Safety check: prevent infinite loop if the length is 0
            if size == 0:
                pos += 3  # Skip past header
                continue

            pos += size

        return (pos, False)

    def insert_statement(self, lineno: int, tokenized_line: bytearray) -> bool:
        """
        Insert a tokenized line at the correct position in the sorted list.

        Args:
            lineno: Line number (0-32767)
            tokenized_line: Tokenized statement including header [ln_lo, ln_hi, len, ...]

        Returns:
            True if successful, False otherwise
        """
        if len(tokenized_line) < 3:
            return False

        pos, found = self.find_insertion_point(lineno)

        # Calculate size difference. Byte 2 is the WHOLE line's length, so it
        # is the old size directly -- see line_size().
        old_len = self.line_size(pos) if found else 0
        # tokenized_line is the complete line: [ln_lo, ln_hi, line_len, ...]
        new_len = len(tokenized_line)

        if new_len > old_len:
            # Expand table - move data upward
            self._expand(pos, new_len - old_len)
        elif new_len < old_len:
            # Contract table - move data downward
            self._contract(pos, old_len - new_len)

        # Copy tokenized data to insertion point
        self.data[pos:pos + new_len] = tokenized_line

        return True

    def delete_statement(self, lineno: int) -> bool:
        """
        Delete a statement by line number.

        Args:
            lineno: Line number to delete

        Returns:
            True if statement was deleted, False if not found
        """
        pos, found = self.find_insertion_point(lineno)

        if not found or pos + 2 >= len(self.data):
            return False

        # Total line size, header included (byte 2 already counts it)
        size = self.line_size(pos)

        # Remove the statement
        del self.data[pos:pos + size]

        return True

    def _expand(self, pos: int, bytes_to_add: int):
        """
        Expand the table at position by moving data upward.

        Args:
            pos: Position in data where expansion starts
            bytes_to_add: Number of bytes to add
        """
        if bytes_to_add <= 0:
            return

        # Find end of table (end marker)
        end = self._find_end_marker()

        # Move data from pos to end upward by bytes_to_add
        if end > pos:
            # Resize the bytearray first
            self.data.extend(b'\x00' * bytes_to_add)

            # Move data upward (reverse order to avoid overwriting)
            for i in range(end - 1, pos - 1, -1):
                self.data[i + bytes_to_add] = self.data[i]

    def _contract(self, pos: int, bytes_to_remove: int):
        """
        Contract the table at position by moving data downward.

        Args:
            pos: Position in data where contraction starts
            bytes_to_remove: Number of bytes to remove
        """
        if bytes_to_remove <= 0:
            return

        # Find end of table (end marker)
        end = self._find_end_marker()

        # Move data from pos to end downward by bytes_to_remove
        if end > pos:
            # Move data downward (forward order)
            for i in range(pos, end - bytes_to_remove):
                self.data[i + bytes_to_remove] = self.data[i]

            # Truncate the bytearray
            del self.data[end - bytes_to_remove:end]

    def list_program(self, start: int = 0, end: int = 32767) -> Iterator[Tuple[int, bytearray]]:
        """
        Iterate through statements in line number order.

        Args:
            start: Minimum line number to include (default 0)
            end: Maximum line number to include (default 32767)

        Yields:
            Tuple of (lineno, statement_data):
            - lineno: Line number (integer)
            - statement_data: Full statement including header [ln_lo, ln_hi, len, ...]
        """
        pos = 0

        while pos + 1 < len(self.data):
            # Check for the end marker (line 32768, NOT line 0 -- see _is_end)
            if self._is_end(pos):
                break

            # Get line number (little-endian)
            lineno = self.data[pos] + (self.data[pos + 1] << 8)

            # Get the whole line's size (byte 2 counts the header too)
            size = self.line_size(pos)

            # Safety check: prevent infinite loop if the length is 0
            if size == 0:
                # Skip this malformed statement and try to recover
                pos += 3
                continue

            # Yield statement if in range
            if start <= lineno <= end:
                yield (lineno, self.data[pos:pos + size])

            # Advance to next line
            pos += size

    def get_statement(self, lineno: int) -> Optional[bytearray]:
        """
        Get a specific statement by line number.

        Args:
            lineno: Line number to retrieve

        Returns:
            Statement data as bytearray, or None if not found
        """
        pos, _ = self.find_insertion_point(lineno)

        if pos + 2 >= len(self.data):
            return None

        # Check for end marker
        if self._is_end(pos):
            return None

        # Get line number
        current_lineno = self.data[pos] + (self.data[pos + 1] << 8)

        if current_lineno != lineno:
            return None

        # Return a copy of the whole line (byte 2 counts the header too)
        return bytearray(self.data[pos:pos + self.line_size(pos)])

    def clear(self):
        """Clear all statements from the table."""
        self.data = bytearray()

    def __len__(self) -> int:
        """Return number of statements in the table."""
        count = 0
        for _ in self.list_program():
            count += 1
        return count

    def __bool__(self) -> bool:
        """Return True if table has any statements."""
        return len(self.data) > 2  # At least end marker

    def to_bytes(self) -> bytes:
        """Return the statement table as immutable bytes."""
        return bytes(self.data)
