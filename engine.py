#!/usr/bin/env python3
"""
Indexed TUI Engine Runner  --  vendored from the `tui-creator` skill
(`assets/tui_template.py`), with ONE local change: menu items may set
`"interactive": true`, which runs the command with the real terminal attached
(no output capture, no spinner, no pager) so curses apps, $EDITOR, `read -rp`
prompts, `fzf` pickers and live-progress scripts work. Everything else -- the
theme engine, subtree clamping, indexed keys, dry-run (`d`), fzf browse
(`use_fzf`), pager -- is unchanged.

Supports:
1. Dynamic JSON spec file loading (python3 engine.py menu.json)
2. Semantic Color Palettes (Primary, Success, Info, Warning, Danger) like Bootstrap
3. Interactive theme selector with Bootstrap-style semantic color swatches
4. Subtree clamping when menu items exceed clamp_threshold
5. Organic theme-matched fzf list selection integration
6. Recommended tool dependency checks (fzf, ripgrep, bat, jq, fd)
7. Cross-platform Braille Spinners, Smooth Sub-block Progress Bars, and Sparkline Histograms
8. Single-Key Immediate Execution (Raw Terminal Mode)
9. Dry-Run Command Inspection Mode ('d' toggle key)
10. Integrated Pager Output Filtering (bat / less -R) for long output (>25 lines)
11. Interactive items ("interactive": true) -- run with the TTY attached
"""
import os
import sys
import time
import json
import shutil
import threading
import subprocess

# --- Try Raw Terminal Mode Input Support ---
HAS_TERMIOS = False
try:
    import termios
    import tty
    HAS_TERMIOS = True
except ImportError:
    HAS_TERMIOS = False

# --- Theme Definitions with Bootstrap-style Semantic Color Tokens & Hex/ANSI for fzf ---
THEMES = {
    "obsidian": {
        "name": "Obsidian Dark",
        "primary": "\033[38;2;168;153;217m",  # Soft Purple
        "success": "\033[38;2;143;191;122m",  # Sage Green
        "info":    "\033[38;2;123;108;166m",  # Lavender Blue
        "warning": "\033[38;2;234;157;52m",   # Amber
        "danger":  "\033[38;2;224;108;117m",  # Red/Coral
        "muted":   "\033[38;2;92;99;112m",   # Slate Gray
        "reset":   "\033[0m",
        "fzf_color": "fg:#a899d9,bg:-1,hl:#ea9d34,fg+:#ffffff,bg+:-1,hl+:#ea9d34,info:#7b6ca6,prompt:#a899d9,pointer:#ea9d34,marker:#8fbf7a,header:#5c6370"
    },
    "dracula": {
        "name": "Dracula",
        "primary": "\033[38;2;189;147;249m",  # Purple
        "success": "\033[38;2;80;250;123m",   # Green
        "info":    "\033[38;2;139;233;253m",  # Cyan
        "warning": "\033[38;2;241;250;140m",  # Yellow
        "danger":  "\033[38;2;255;85;85m",    # Red
        "muted":   "\033[38;2;98;114;164m",   # Comment Gray
        "reset":   "\033[0m",
        "fzf_color": "fg:#bd93f9,bg:-1,hl:#ff79c6,fg+:#ffffff,bg+:-1,hl+:#50fa7b,info:#8be9fd,prompt:#bd93f9,pointer:#ff79c6,marker:#50fa7b,header:#6272a4"
    },
    "nord": {
        "name": "Nordic Frost",
        "primary": "\033[38;2;136;192;208m",  # Frost Blue
        "success": "\033[38;2;163;190;140m",  # Green
        "info":    "\033[38;2;129;161;193m",  # Blue
        "warning": "\033[38;2;235;203;139m",  # Gold
        "danger":  "\033[38;2;191;97;106m",   # Red
        "muted":   "\033[38;2;76;86;106m",    # Polar Night Gray
        "reset":   "\033[0m",
        "fzf_color": "fg:#88c0d0,bg:-1,hl:#ebcb8b,fg+:#ffffff,bg+:-1,hl+:#a3be8c,info:#81a1c1,prompt:#88c0d0,pointer:#ebcb8b,marker:#a3be8c,header:#4c566a"
    },
    "cyberpunk": {
        "name": "Cyberpunk Neon",
        "primary": "\033[38;2;255;0;127m",    # Neon Pink
        "success": "\033[38;2;57;255;20m",    # Neon Green
        "info":    "\033[38;2;0;240;255m",    # Electric Cyan
        "warning": "\033[38;2;255;230;0m",   # Yellow
        "danger":  "\033[38;2;255;7;58m",     # Red
        "muted":   "\033[38;2;100;100;120m",  # Steel Gray
        "reset":   "\033[0m",
        "fzf_color": "fg:#ff007f,bg:-1,hl:#ffe600,fg+:#ffffff,bg+:-1,hl+:#00f0ff,info:#00f0ff,prompt:#ff007f,pointer:#ffe600,marker:#39ff14,header:#646478"
    },
    "emerald": {
        "name": "Emerald Forest",
        "primary": "\033[38;2;46;204;113m",   # Emerald Green
        "success": "\033[38;2;39;174;96m",    # Dark Emerald
        "info":    "\033[38;2;26;188;156m",   # Turquoise
        "warning": "\033[38;2;241;196;15m",   # Gold
        "danger":  "\033[38;2;231;76;60m",    # Red
        "muted":   "\033[38;2;127;140;141m",  # Gray
        "reset":   "\033[0m",
        "fzf_color": "fg:#2ecc71,bg:-1,hl:#f1c40f,fg+:#ffffff,bg+:-1,hl+:#1abc9c,info:#1abc9c,prompt:#2ecc71,pointer:#f1c40f,marker:#27ae60,header:#7f8c8d"
    }
}

ACTIVE_THEME = THEMES["obsidian"]
DRY_RUN_MODE = False

# --- Braille Spinner Frames ---
BRAILLE_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
BLOCK_SUBLEVELS = [" ", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"]
SPARKLINE_BLOCKS = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

RECOMMENDED_TOOLS = {
    "fzf": "Fuzzy list selection (sudo pacman -S fzf / apt install fzf)",
    "rg": "Ripgrep fast search (sudo pacman -S ripgrep / apt install ripgrep)",
    "fd": "Fast directory search (sudo pacman -S fd / apt install fd-find)",
    "bat": "Syntax highlighted viewer (sudo pacman -S bat / apt install bat)",
    "jq": "JSON processor (sudo pacman -S jq / apt install jq)"
}

def set_theme(theme_name):
    global ACTIVE_THEME
    if theme_name in THEMES:
        ACTIVE_THEME = THEMES[theme_name]

def get_single_key(prompt_str=""):
    """Reads a single keypress without waiting for ENTER if termios is available."""
    if prompt_str:
        print(prompt_str, end="", flush=True)
    if HAS_TERMIOS and sys.stdin.isatty():
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        print(ch)
        return ch.strip().lower()
    else:
        return input().strip().lower()

def check_recommended_tools():
    missing = {tool: desc for tool, desc in RECOMMENDED_TOOLS.items() if not shutil.which(tool)}
    if missing:
        M = ACTIVE_THEME['warning']
        R = ACTIVE_THEME['reset']
        print(f"\n{M}💡 Note: Some recommended tools are not installed on your system:{R}")
        for tool, desc in missing.items():
            print(f"   • {M}{tool}{R}: {desc}")
        print()

def render_braille_spinner(label, stop_event):
    idx = 0
    P = ACTIVE_THEME['primary']
    R = ACTIVE_THEME['reset']
    while not stop_event.is_set():
        frame = BRAILLE_SPINNER[idx % len(BRAILLE_SPINNER)]
        sys.stdout.write(f"\r  {P}{frame}{R} {label}...")
        sys.stdout.flush()
        idx += 1
        time.sleep(0.08)
    sys.stdout.write("\r" + " " * (len(label) + 15) + "\r")
    sys.stdout.flush()

def display_with_pager(text_content):
    """Pipes text_content into bat or less -R if output exceeds terminal height."""
    lines = text_content.strip().splitlines()
    term_height = shutil.get_terminal_size().lines

    if len(lines) > (term_height - 5):
        if shutil.which("bat"):
            p = subprocess.Popen(["bat", "--paging=always", "--style=plain"], stdin=subprocess.PIPE, text=True)
            p.communicate(input=text_content)
        elif shutil.which("less"):
            p = subprocess.Popen(["less", "-R"], stdin=subprocess.PIPE, text=True)
            p.communicate(input=text_content)
        else:
            print(text_content)
    else:
        print(text_content)

def fzf_select(options_list, prompt_text="Select option: ", header_text=""):
    if not shutil.which("fzf"):
        print(f"\n{ACTIVE_THEME['warning']}⚠ fzf is not installed. Displaying list:{ACTIVE_THEME['reset']}\n")
        for i, item in enumerate(options_list, 1):
            print(f"  {i}) {item}")
        try:
            idx = int(input(f"\n{ACTIVE_THEME['primary']}Enter choice number: {ACTIVE_THEME['reset']}")) - 1
            if 0 <= idx < len(options_list):
                return options_list[idx]
        except (ValueError, IndexError):
            return None
        return None

    color_flag = f"--color={ACTIVE_THEME.get('fzf_color', '')}"
    fzf_cmd = [
        "fzf",
        "--height=50%",
        "--layout=reverse",
        "--border=rounded",
        color_flag,
        f"--prompt={prompt_text} ",
        f"--header={header_text}" if header_text else "--header=Use arrow keys or type to filter"
    ]

    try:
        input_data = "\n".join(options_list)
        process = subprocess.Popen(
            fzf_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True
        )
        stdout, _ = process.communicate(input=input_data)
        if process.returncode == 0 and stdout:
            return stdout.strip()
    except Exception as e:
        print(f"{ACTIVE_THEME['danger']}fzf selection error: {e}{ACTIVE_THEME['reset']}")

    return None

def render_color_swatches():
    P = ACTIVE_THEME['primary']
    R = ACTIVE_THEME['reset']

    print(f"\n{P}═══ Theme Palette Preview (Semantic Roles) ═══{R}\n")
    print(f"  {ACTIVE_THEME['muted']}Swatches Order: [Primary] [Success] [Info] [Warning] [Danger]{R}\n")

    for key, theme in THEMES.items():
        swatch_p = f"{theme['primary']}██{R}"
        swatch_s = f"{theme['success']}██{R}"
        swatch_i = f"{theme['info']}██{R}"
        swatch_w = f"{theme['warning']}██{R}"
        swatch_d = f"{theme['danger']}██{R}"

        is_active = " [Active]" if theme == ACTIVE_THEME else ""
        print(f"  [{key:10s}]  {swatch_p} {swatch_s} {swatch_i} {swatch_w} {swatch_d}  -- {theme['name']}{is_active}")
    print()

def clear_screen():
    os.system('clear')

def print_header(title):
    P = ACTIVE_THEME['primary']
    I = ACTIVE_THEME['info']
    W = ACTIVE_THEME['warning']
    R = ACTIVE_THEME['reset']

    width = 62
    dry_tag = f" {W}[DRY-RUN ON]{R}" if DRY_RUN_MODE else ""
    full_title = f"{title}{dry_tag}"

    print(f"{P}╔{'═' * (width - 2)}╗{R}")
    print(f"{P}║{' ' * (width - 2)}║{R}")
    padding = (width - 4 - len(title)) // 2
    extra = (width - 4 - len(title)) % 2
    print(f"{P}║{' ' * padding}{I}{title}{P}{' ' * (padding + extra)}║{R}")
    print(f"{P}║{' ' * (width - 2)}║{R}")
    print(f"{P}╚{'═' * (width - 2)}╝{R}\n")

def pause():
    print()
    input(f"{ACTIVE_THEME['muted']}Press ENTER to continue...{ACTIVE_THEME['reset']}")

def count_total_options(menu_data):
    total = 0
    for sec in menu_data.get("sections", []):
        total += len(sec.get("items", []))
    return total

def render_clamped_parent_menu(menu_data):
    clear_screen()
    print_header(menu_data.get("title", "Indexed TUI Manager"))

    P = ACTIVE_THEME['primary']
    I = ACTIVE_THEME['info']
    W = ACTIVE_THEME['warning']
    D = ACTIVE_THEME['danger']
    M = ACTIVE_THEME['muted']
    R = ACTIVE_THEME['reset']

    print(f"{P}═══ Main Menu (Clamped View - Select Category) ═══{R}\n")

    sections = menu_data.get("sections", [])
    action_map = {}

    for idx, section in enumerate(sections, start=1):
        sec_id = str(idx)
        sec_title = section.get("title", f"Category {sec_id}")
        item_count = len(section.get("items", []))
        action_map[sec_id] = section
        print(f"  {I}{sec_id}{R})  {P}📁 {sec_title}{R} {M}({item_count} options){R}")

    print(f"\n  {W}d{R})  {I}🔍 Toggle Dry-Run Mode ({'ON' if DRY_RUN_MODE else 'OFF'}){R}")
    print(f"  {W}t{R})  {I}🎨 Select UI Theme{R}")
    print(f"  {D}0{R})  Exit\n")

    return action_map

def render_subtree_menu(section, sec_idx):
    clear_screen()
    sec_title = section.get("title", f"Category {sec_idx}")
    print_header(f"Category: {sec_title}")

    P = ACTIVE_THEME['primary']
    I = ACTIVE_THEME['info']
    W = ACTIVE_THEME['warning']
    D = ACTIVE_THEME['danger']
    R = ACTIVE_THEME['reset']

    action_map = {}
    items = section.get("items", [])

    print(f"{P}═══ Subtree Options ═══{R}\n")
    for item in items:
        key = item.get("key")
        label = item.get("label")
        action_code = f"{sec_idx}{key}".lower()
        action_map[action_code] = item
        print(f"  {I}{action_code}{R})  {P}{label}{R}")

    print(f"\n  {W}d{R})  {I}🔍 Toggle Dry-Run Mode ({'ON' if DRY_RUN_MODE else 'OFF'}){R}")
    print(f"  {I}b{R})  Back to Parent Menu")
    print(f"  {D}0{R})  Exit\n")

    return action_map

def render_expanded_menu(menu_data):
    clear_screen()
    print_header(menu_data.get("title", "Indexed TUI Manager"))

    P = ACTIVE_THEME['primary']
    I = ACTIVE_THEME['info']
    W = ACTIVE_THEME['warning']
    D = ACTIVE_THEME['danger']
    R = ACTIVE_THEME['reset']

    action_map = {}
    sections = menu_data.get("sections", [])

    for idx, section in enumerate(sections, start=1):
        sec_id = str(idx)
        sec_title = section.get("title")
        print(f"{P}{sec_id}{R})   {I}⚙️  {sec_title}{R}")

        items = section.get("items", [])
        for item in items:
            key = item.get("key")
            label = item.get("label")
            action_code = f"{sec_id}{key}".lower()
            action_map[action_code] = item
            print(f"      {I}{key}{R})  {P}{label}{R}")
        print()

    print(f"  {W}d{R})  {I}🔍 Toggle Dry-Run Mode ({'ON' if DRY_RUN_MODE else 'OFF'}){R}")
    print(f"  {W}t{R})  {I}🎨 Select UI Theme{R}")
    print(f"  {D}0{R})  Exit\n")

    return action_map

def run_task(item):
    global DRY_RUN_MODE
    label = item.get("label")
    cmd = item.get("cmd")
    cwd = item.get("cwd", os.getcwd())
    use_fzf = item.get("use_fzf", False)
    interactive = item.get("interactive", False)

    print_header(label)

    # --- Feature 2: Dry-Run Inspection ---
    if DRY_RUN_MODE:
        print(f"{ACTIVE_THEME['warning']}🔍 [DRY-RUN INSPECTION MODE]{ACTIVE_THEME['reset']}\n")
        print(f"  {ACTIVE_THEME['info']}Label:{ACTIVE_THEME['reset']}       {label}")
        print(f"  {ACTIVE_THEME['info']}Command:{ACTIVE_THEME['reset']}     {cmd}")
        print(f"  {ACTIVE_THEME['info']}Directory:{ACTIVE_THEME['reset']}   {cwd}")
        print(f"  {ACTIVE_THEME['info']}fzf Pipe:{ACTIVE_THEME['reset']}    {use_fzf}")
        print(f"  {ACTIVE_THEME['info']}Interactive:{ACTIVE_THEME['reset']} {interactive}")
        print(f"\n{ACTIVE_THEME['success']}Command inspect complete. No changes were executed.{ACTIVE_THEME['reset']}")
        pause()
        return

    # --- Local addition: interactive items run with the TTY attached ---
    # No output capture, no spinner, no pager -- the child owns stdin/stdout/
    # stderr. Required for curses apps, $EDITOR, `read -rp`, fzf pickers piped
    # into a consumer, and anything that streams progress.
    if interactive:
        print(f"{ACTIVE_THEME['info']}Executing (interactive):{ACTIVE_THEME['reset']} {cmd}\n")
        try:
            rc = subprocess.run(cmd, shell=True, cwd=cwd).returncode
            print()
            if rc == 0:
                print(f"{ACTIVE_THEME['success']}✓ Task finished successfully (Code 0){ACTIVE_THEME['reset']}")
            else:
                print(f"{ACTIVE_THEME['danger']}✗ Task exited with code: {rc}{ACTIVE_THEME['reset']}")
        except Exception as e:
            print(f"{ACTIVE_THEME['danger']}✗ Execution error: {e}{ACTIVE_THEME['reset']}")
        pause()
        return

    print(f"{ACTIVE_THEME['info']}Executing:{ACTIVE_THEME['reset']} {cmd}\n")

    stop_spinner = threading.Event()
    spinner_thread = threading.Thread(target=render_braille_spinner, args=(f"Processing {label}", stop_spinner))
    spinner_thread.start()

    try:
        if use_fzf:
            p = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            output, err = p.communicate()

            stop_spinner.set()
            spinner_thread.join()

            if output:
                lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
                selected = fzf_select(lines, prompt_text=f"Select ({label}): ", header_text=f"Filter results for {label}")
                if selected:
                    print(f"\n{ACTIVE_THEME['success']}✓ Selected item:{ACTIVE_THEME['reset']} {selected}")
                else:
                    print(f"\n{ACTIVE_THEME['warning']}Selection cancelled.{ACTIVE_THEME['reset']}")
            else:
                print(f"{ACTIVE_THEME['warning']}No output returned by command.{ACTIVE_THEME['reset']}")
        else:
            p = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            output, err = p.communicate()

            stop_spinner.set()
            spinner_thread.join()

            print()
            if p.returncode == 0:
                print(f"{ACTIVE_THEME['success']}✓ Task finished successfully (Code 0){ACTIVE_THEME['reset']}\n")
                if output:
                    display_with_pager(output)
            else:
                print(f"{ACTIVE_THEME['danger']}✗ Task failed with exit code: {p.returncode}{ACTIVE_THEME['reset']}\n")
                if err or output:
                    display_with_pager(err or output)
    except Exception as e:
        stop_spinner.set()
        spinner_thread.join()
        print(f"{ACTIVE_THEME['danger']}✗ Execution error: {e}{ACTIVE_THEME['reset']}")

    pause()

def handle_theme_selection():
    clear_screen()
    print_header("Theme Selector")
    render_color_swatches()
    choice = input(f"{ACTIVE_THEME['primary']}Enter theme key (or press ENTER to keep): {ACTIVE_THEME['reset']}").strip().lower()
    if choice in THEMES:
        set_theme(choice)
        print(f"\n{ACTIVE_THEME['success']}Theme updated to {THEMES[choice]['name']}!{ACTIVE_THEME['reset']}")
        pause()

def load_menu_spec():
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        with open(sys.argv[1], 'r') as f:
            return json.load(f)

    return {
        "title": "Indexed TUI Manager",
        "clamp_threshold": 10,
        "sections": [
            {
                "title": "System Diagnostics",
                "items": [
                    {"key": "1", "label": "Disk Usage Summary", "cmd": "df -h"},
                    {"key": "2", "label": "Memory & Swap Info", "cmd": "free -h"},
                    {"key": "a", "label": "Active System Services", "cmd": "systemctl list-units --type=service --state=running | head -20", "use_fzf": True}
                ]
            },
            {
                "title": "Network & Ports",
                "items": [
                    {"key": "1", "label": "Listening Ports", "cmd": "ss -tulpn", "use_fzf": True},
                    {"key": "2", "label": "IP Routing Table", "cmd": "ip route"}
                ]
            }
        ]
    }

def main():
    global DRY_RUN_MODE
    check_recommended_tools()
    menu_data = load_menu_spec()
    clamp_threshold = menu_data.get("clamp_threshold", 10)

    if "theme" in menu_data:
        set_theme(menu_data["theme"])

    while True:
        total_options = count_total_options(menu_data)

        if total_options > clamp_threshold:
            parent_map = render_clamped_parent_menu(menu_data)
            choice = get_single_key(f"{ACTIVE_THEME['primary']}Select category option (e.g., 1, d, t, 0): {ACTIVE_THEME['reset']}")

            if choice == '0':
                break
            elif choice == 'd':
                DRY_RUN_MODE = not DRY_RUN_MODE
            elif choice == 't':
                handle_theme_selection()
            elif choice in parent_map:
                sec_idx = choice
                section = parent_map[sec_idx]
                while True:
                    sub_map = render_subtree_menu(section, sec_idx)
                    sub_choice = input(f"{ACTIVE_THEME['primary']}Select option (e.g., {sec_idx}1, b, 0): {ACTIVE_THEME['reset']}").strip().lower()
                    if sub_choice == '0':
                        sys.exit(0)
                    elif sub_choice == 'd':
                        DRY_RUN_MODE = not DRY_RUN_MODE
                        break
                    elif sub_choice == 'b':
                        break
                    elif sub_choice in sub_map:
                        run_task(sub_map[sub_choice])
                    else:
                        print(f"{ACTIVE_THEME['danger']}Invalid option.{ACTIVE_THEME['reset']}")
                        subprocess.run(["sleep", "1"])
            else:
                print(f"{ACTIVE_THEME['danger']}Invalid category selection.{ACTIVE_THEME['reset']}")
                subprocess.run(["sleep", "1"])
        else:
            action_map = render_expanded_menu(menu_data)
            choice = input(f"{ACTIVE_THEME['primary']}Select option (e.g., 11, 1a, d, t, 0): {ACTIVE_THEME['reset']}").strip().lower()

            if choice == '0':
                break
            elif choice == 'd':
                DRY_RUN_MODE = not DRY_RUN_MODE
            elif choice == 't':
                handle_theme_selection()
            elif choice in action_map:
                run_task(action_map[choice])
            else:
                print(f"{ACTIVE_THEME['danger']}Invalid option.{ACTIVE_THEME['reset']}")
                subprocess.run(["sleep", "1"])

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nGoodbye!\n")
        sys.exit(0)
