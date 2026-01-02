import re


def get_utf16_length(s):
    """Get length in UTF-16 code units."""
    return len(s.encode('utf-16-le')) // 2


def convert_telegram_markdown(text):
    """
    Convert Telegram markdown to plain text + signal-cli textStyles.
    Uses UTF-16 code units for positions (required by signal-cli).
    Handles nested formatting.
    """
    styles = []
    result_text = ""

    # Using [\s\S]+? instead of .+? to match newlines
    patterns = [
        (r'\*\*([\s\S]+?)\*\*', 'BOLD'),
        (r'__([\s\S]+?)__', 'ITALIC'),
        (r'~~([\s\S]+?)~~', 'STRIKETHROUGH'),
        (r'\|\|([\s\S]+?)\|\|', 'SPOILER'),
        (r'`([\s\S]+?)`', 'MONOSPACE'),
        (r'\[([\s\S]+?)\]\([^\)]+\)', 'ITALIC'),
    ]

    combined = '|'.join(f'({p})' for p, _ in patterns)

    last_end = 0
    for match in re.finditer(combined, text):
        result_text += text[last_end:match.start()]

        matched_text = match.group(0)

        for pattern, style in patterns:
            inner_match = re.fullmatch(pattern, matched_text)
            if inner_match:
                inner_text = inner_match.group(1)

                # Recursively parse inner text for nested formatting
                parsed_inner, inner_styles = convert_telegram_markdown(inner_text)

                start_pos = get_utf16_length(result_text)
                length = get_utf16_length(parsed_inner)

                styles.append(f"{start_pos}:{length}:{style}")

                # Adjust inner style positions and add them
                for inner_style in inner_styles:
                    inner_start, inner_len, inner_style_name = inner_style.split(':')
                    adjusted_start = start_pos + int(inner_start)
                    styles.append(f"{adjusted_start}:{inner_len}:{inner_style_name}")

                result_text += parsed_inner
                break

        last_end = match.end()

    result_text += text[last_end:]

    return result_text, styles
