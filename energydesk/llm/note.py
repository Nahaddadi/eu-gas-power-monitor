"""The desk note document: parsing the model output and rendering it."""

import re
from dataclasses import dataclass, field

BLOCK_TAGS = ("BOTTOM LINE", "GAS", "CARBON", "POWER", "RISKS")


@dataclass
class DeskNote:
    """One drafted desk note, split into its tagged blocks."""

    bottom_line: str = ""
    gas: str = ""
    carbon: str = ""
    power: str = ""
    risks: list[str] = field(default_factory=list)
    analyst_line: str = ""

    @classmethod
    def parse(cls, raw: str) -> "DeskNote":
        """Parse tagged-block text; tolerate blank lines and stray fences."""
        raw = cls._strip_fences(raw.strip())
        analyst_line = cls._parse_analyst(raw)
        if analyst_line:
            # Drop the analyst line so it cannot be read into the risks block.
            raw = re.sub(r"^\s*[>#\-*`*\s]*ANALYST:.*$", "", raw,
                         flags=re.MULTILINE).strip()
        blocks = cls._split_blocks(raw)
        return cls(
            bottom_line=blocks.get("BOTTOM LINE", ""),
            gas=blocks.get("GAS", ""),
            carbon=blocks.get("CARBON", ""),
            power=blocks.get("POWER", ""),
            risks=cls._parse_risks(blocks.get("RISKS", "")),
            analyst_line=analyst_line,
        )

    @classmethod
    def found_tags(cls, raw: str) -> list[str]:
        """Which section tags a raw draft carried, for failure diagnostics."""
        return sorted(cls._split_blocks(cls._strip_fences(raw.strip())))

    # -- parsing helpers ------------------------------------------------------

    @staticmethod
    def _strip_fences(raw: str) -> str:
        return "\n".join(
            line for line in raw.splitlines()
            if line.strip().strip("`~").strip() != ""
        )

    @staticmethod
    def _split_blocks(raw: str) -> dict[str, str]:
        """Map each tag to the text that follows it, up to the next tag.

        Tag lines survive light model decoration - bold markers, heading
        hashes, list dashes, trailing colons - because models rarely keep
        them bare. Bracket contents must be letters/spaces, so inline
        citation markers like [2] can never be mistaken for sections.
        """
        pattern = re.compile(
            r"^[\s>#\-*_`~]*\[([A-Za-z][A-Za-z ]*)\][\s:*_`~]*$",
            re.MULTILINE,
        )
        matches = list(pattern.finditer(raw))
        blocks: dict[str, str] = {}
        for i, match in enumerate(matches):
            tag = match.group(1).strip().upper()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
            body = raw[match.end(): end].strip()
            if tag in BLOCK_TAGS:
                blocks[tag] = body
        return blocks

    @staticmethod
    def _parse_risks(body: str) -> list[str]:
        risks = []
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("-"):
                risks.append(line.removeprefix("-").strip())
            elif line and risks:
                # continuation of the previous bullet
                risks[-1] += " " + line
        return risks

    @staticmethod
    def _parse_analyst(raw: str) -> str:
        match = re.search(r"^[\s>#\-*`*_]*ANALYST\s*:\s*(.+)$",
                          raw, re.MULTILINE)
        return (match.group(1).strip().strip("*`_ ").strip()
                if match else "")

    # -- rendering --------------------------------------------------------------

    def render_plain(self) -> str:
        """Back to the tagged-block text format, for saving to file."""
        parts = [
            "[BOTTOM LINE]", self.bottom_line, "",
            "[GAS]", self.gas, "",
            "[CARBON]", self.carbon, "",
            "[POWER]", self.power, "",
            "[RISKS]",
        ]
        parts += [f"- {r}" for r in self.risks]
        parts += ["", f"ANALYST: {self.analyst_line}"]
        return "\n".join(parts)

    def render_markdown(self) -> str:
        """Markdown version for the Streamlit app."""
        sections = [
            ("Bottom Line", self.bottom_line),
            ("Gas", self.gas),
            ("Carbon", self.carbon),
            ("Power", self.power),
        ]
        md = [f"## {title}\n\n{text}" for title, text in sections if text]
        if self.risks:
            md.append("## Risks\n\n" + "\n".join(f"- {r}" for r in self.risks))
        if self.analyst_line:
            md.append(f"> **Analyst:** {self.analyst_line}")
        return "\n\n".join(md)
