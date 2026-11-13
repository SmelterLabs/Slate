# Put the Slate guide (the Slate skill) into every agent app on this machine, from the repo copy.
#
# The guide's one source is integrations/claude-code/skills/slate/ in this repo. This script takes
# it from a git ref (default origin/main, so a branch checked out here can't change what apps
# load) and replaces every installed copy. One run covers:
#
#   This machine
#     - Claude Code / Claude Desktop: ~/.claude/skills/slate, and the skills folder of any
#       additional ~/.claude-* config directory (a junction or symlink there is left as it is; a
#       directory with "retired" in its name is skipped).
#     - Codex: every ~/.codex* home that has a config.toml gets skills/slate, plus a short managed
#       block in its AGENTS.md that points at the skill.
#     - Gemini CLI and Cursor: ~/.gemini/skills/slate, ~/.cursor/skills/slate (if those folders exist).
#     - Hermes Agent: %LOCALAPPDATA%\hermes\skills\productivity\slate and every profile's.
#   Remote Linux/macOS hosts (over ssh, -RemoteHost, optional, repeatable)
#     - Hermes ~/.hermes and every profile, ~/.claude, ~/.codex if present. The files are sent
#       from here, so each host gets the same ref, into a private folder made there with
#       `mktemp -d` (never a fixed /tmp name another user could swap), and install-remote.sh
#       runs from it; the folder is removed afterwards.
#
# Old local copies are moved to ~\.slate-skill-backup-<time>\ first (remote: ~/.slate-skill-backup-<time>).
# -WhatIf fetches nothing and changes nothing.
#
#   pwsh integrations/claude-code/skills/install.ps1 [-Ref origin/main] [-RemoteHost my-server] [-Exclude word] [-BoardUrl https://slate.example.com] [-WhatIf]
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Ref = 'origin/main',
    [string]$Repo = (Join-Path $PSScriptRoot '..\..\..'),
    [string[]]$RemoteHost = @(),
    # Leave alone any ~/.claude-* config directory whose name contains one of these words.
    [string[]]$Exclude = @(),
    # Your board's address, named in the Codex pointer block so a pasted link triggers the skill.
    [string]$BoardUrl = ''
)
$ErrorActionPreference = 'Stop'
$bsdtar = Join-Path $env:SystemRoot 'System32\tar.exe'   # not Git's GNU tar, which reads C: as a host
$repo = (Resolve-Path $Repo).Path
$src = 'integrations/claude-code/skills/slate'
$begin = '<!-- slate-guide:start (managed by the Slate repo''s skill installer; edit the repo, not this) -->'
$end = '<!-- slate-guide:end -->'

if ($Ref -like 'origin/*' -and $PSCmdlet.ShouldProcess($repo, "git fetch $Ref")) { git -C $repo fetch -q origin ($Ref -replace '^origin/', '') }
$stage = Join-Path ([IO.Path]::GetTempPath()) ("slate-skill-" + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($stage) | Out-Null
try {
$tar = Join-Path $stage 'skill.tar'
git -C $repo archive --format=tar -o $tar $Ref $src
if ($LASTEXITCODE -ne 0) { throw "git archive $Ref $src failed" }
& $bsdtar -xf $tar -C $stage
$fresh = Join-Path $stage ($src -replace '/', '\')
if (-not (Test-Path (Join-Path $fresh 'SKILL.md'))) { throw "no SKILL.md at ${Ref}:$src" }
$sha = (git -C $repo rev-parse --short $Ref).Trim()
"Slate guide from $Ref ($sha)"

# ---- this machine ----------------------------------------------------------------------------
$targets = [Collections.Generic.List[string]]::new()
$targets.Add((Join-Path $HOME '.claude\skills\slate'))
foreach ($root in Get-ChildItem $HOME -Directory -Force -Filter '.claude-*') {
    if ($Exclude | Where-Object { $root.Name -like "*$_*" }) { continue }
    $skills = Join-Path $root.FullName 'skills'
    if (-not (Test-Path $skills)) { continue }
    $t = Join-Path $skills 'slate'
    if ((Test-Path $t) -and (Get-Item $t -Force).LinkType) { continue }   # a link to another root
    $targets.Add($t)
}
$codexHomes = @(Get-ChildItem $HOME -Directory -Force -Filter '.codex*' |
    Where-Object { Test-Path (Join-Path $_.FullName 'config.toml') })
foreach ($c in $codexHomes) { $targets.Add((Join-Path $c.FullName 'skills\slate')) }
foreach ($d in '.gemini', '.cursor') {
    $skills = Join-Path $HOME "$d\skills"
    if (Test-Path $skills) { $targets.Add((Join-Path $skills 'slate')) }
}
$hermes = Join-Path $env:LOCALAPPDATA 'hermes'
if (Test-Path $hermes) {
    $targets.Add((Join-Path $hermes 'skills\productivity\slate'))
    $profiles = Join-Path $hermes 'profiles'
    if (Test-Path $profiles) {
        foreach ($p in Get-ChildItem $profiles -Directory | Where-Object Name -notlike '.*') {
            $targets.Add((Join-Path $p.FullName 'skills\productivity\slate'))
        }
    }
}

# Kept in the home folder, not %TEMP%, which cleanup tools empty.
$backup = Join-Path $HOME (".slate-skill-backup-" + (Get-Date -Format yyyyMMdd-HHmmss))
$i = 0
foreach ($t in $targets) {
    if ($PSCmdlet.ShouldProcess($t, "install slate skill $sha")) {
        if (Test-Path $t) {
            $i++
            $dest = Join-Path $backup "$i"
            New-Item -ItemType Directory -Force $dest | Out-Null
            Set-Content (Join-Path $dest 'FROM.txt') $t
            Move-Item $t (Join-Path $dest 'slate')
        }
        New-Item -ItemType Directory -Force (Split-Path $t) | Out-Null
        Copy-Item $fresh $t -Recurse
        "installed -> $t"
    }
}

# Codex has skills, but its AGENTS.md is what every session reads first: point it at the skill.
$linkHint = if ($BoardUrl) { " or a ``$BoardUrl`` link" } else { '' }
foreach ($c in $codexHomes) {
    $agents = Join-Path $c.FullName 'AGENTS.md'
    $skillPath = Join-Path $c.FullName 'skills\slate\SKILL.md'
    $block = @"
$begin
## Tasks: Slate

The task board is **Slate**. For any task action (file a task, read or work one, comment,
change status, what's due) and whenever a message holds a Slate item id$linkHint, read and
follow the ``slate`` skill: ``$skillPath``.
$end
"@
    $old = if (Test-Path $agents) { Get-Content -Raw $agents } else { '' }
    $pattern = '(?s)' + [regex]::Escape($begin) + '.*?' + [regex]::Escape($end)
    $new = if ($old -match $pattern) { [regex]::Replace($old, $pattern, { $block }) }
           else { ($old.TrimEnd() + "`n`n" + $block).TrimStart() }
    $new = $new.TrimEnd() + "`n"
    if ($new -ne $old -and $PSCmdlet.ShouldProcess($agents, 'write the Slate pointer block')) {
        [IO.File]::WriteAllText($agents, $new)
        "pointer block -> $agents"
    }
}
if ($i) { "old copies saved in $backup" }

# ---- remote hosts ----------------------------------------------------------------------------
if ($RemoteHost.Count) {
    $remoteTar = Join-Path $stage 'slate-skill.tar'
    & $bsdtar -cf $remoteTar -C (Split-Path $fresh) slate
    $script = Join-Path $PSScriptRoot 'install-remote.sh'
    foreach ($h in $RemoteHost) {
        if ($PSCmdlet.ShouldProcess($h, "install slate skill $sha over ssh")) {
            # A folder only this user can write (mktemp -d is mode 0700), so nobody else on the
            # host can put their own script where ours is run.
            $dir = "$(ssh $h 'mktemp -d')".Trim()
            if ($LASTEXITCODE -ne 0 -or $dir -notmatch '^/[\w./-]+$') { throw "mktemp on $h failed" }
            try {
                scp -q $remoteTar $script "${h}:$dir/"
                if ($LASTEXITCODE -ne 0) { throw "scp to $h failed" }
                ssh $h bash "$dir/install-remote.sh" "$dir/slate-skill.tar" $sha
                if ($LASTEXITCODE -ne 0) { throw "install-remote.sh on $h failed" }
            } finally {
                ssh $h rm -rf "$dir"
            }
        }
    }
}
} finally {
    [IO.Directory]::Delete($stage, $true)
}
