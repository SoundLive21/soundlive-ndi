[CmdletBinding()]
param([string]$VncMsi, [string]$Depuis)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()

$payload = $PSScriptRoot
$version = $null
$title = 'Installation de la régie SoundLive-NDI'
$installDir = Join-Path $env:LOCALAPPDATA 'Programs\soundlive-ndi'
$maintenance = Join-Path $installDir '.installation'
$registry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SoundLive-NDI-Regie'
$appExe = Join-Path $installDir 'SoundLive-NDI.exe'
$log = Join-Path $env:LOCALAPPDATA 'SoundLive-NDI-installation.log'
$pythonVersion = '3.13.17'
$pythonSha256 = '5da012101ba2e17113a9a1cd901e9ce1441086f2b7ecb5de06ec94163899a190'
$pythonUrl = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-amd64.exe"

function Write-Log([string]$Message) {
    Add-Content -LiteralPath $log -Value ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $Message) -Encoding UTF8
}

function Modal([string]$Message, [string]$Buttons, [string]$Icon) {
    [Windows.Forms.MessageBox]::Show($Message, $title, $Buttons, $Icon)
}

function Set-Status([string]$Message) {
    $label.Text = $Message
    Write-Log $Message
    [Windows.Forms.Application]::DoEvents()
}

function Invoke-Hidden([string]$Executable, [string]$Arguments) {
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Executable
    $startInfo.Arguments = $Arguments
    $startInfo.WorkingDirectory = $work
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = [Text.Encoding]::UTF8
    $startInfo.StandardErrorEncoding = [Text.Encoding]::UTF8
    $startInfo.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'

    $process = [Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) { throw "Impossible de démarrer $Executable." }
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        while (-not $process.WaitForExit(100)) {
            if ($progress) { [Windows.Forms.Application]::DoEvents() }
        }
        $exitCode = $process.ExitCode
        if ($null -eq $exitCode) { throw "Impossible de lire le code de sortie de $Executable." }
        $output = $stdout.GetAwaiter().GetResult()
        $errors = $stderr.GetAwaiter().GetResult()
    } finally {
        $process.Dispose()
    }

    $program = [IO.Path]::GetFileName($Executable)
    Write-Log "$program, code de sortie $exitCode."
    if ($errors.Trim()) { Write-Log ($errors.TrimEnd()) }
    if ($exitCode -ne 0 -and $output.Trim()) { Write-Log ($output.TrimEnd()) }
    [pscustomobject]@{ ExitCode = $exitCode; Output = @($output -split '\r?\n' | Where-Object { $_.Trim() }) }
}

function Test-Python([string]$Executable) {
    if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return }
    $probe = "import sys, struct, tkinter as tk; assert (3, 10) <= sys.version_info and struct.calcsize('P') == 8; tk.Tcl().eval('info patchlevel'); print(sys.executable)"
    $result = Invoke-Hidden $Executable "-c `"$probe`""
    if ($result.ExitCode -ne 0 -or -not $result.Output) { return }
    $path = $result.Output[-1].Trim()
    $windowed = Join-Path (Split-Path -Parent $path) 'pythonw.exe'
    if ((Test-Path -LiteralPath $path -PathType Leaf) -and
        (Test-Path -LiteralPath $windowed -PathType Leaf)) { return $path }
}

function Find-Python {
    $registrations = @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore')
    foreach ($hive in $registrations) {
        if (-not (Test-Path -LiteralPath $hive)) { continue }
        $keys = Get-ChildItem -LiteralPath $hive |
            Where-Object { $_.PSChildName -match '^3\.\d+$' } |
            Sort-Object -Property { [version]$_.PSChildName } -Descending
        foreach ($key in $keys) {
            $installKey = Join-Path $key.PSPath 'InstallPath'
            if (-not (Test-Path -LiteralPath $installKey)) { continue }
            $directory = (Get-Item -LiteralPath $installKey).GetValue('')
            if (-not $directory) { continue }
            $usable = Test-Python (Join-Path $directory 'python.exe')
            if ($usable) { return $usable }
        }
    }

    $standard = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
    $usable = Test-Python $standard
    if ($usable) { return $usable }

    $python = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike '*\Microsoft\WindowsApps\*') { Test-Python $python.Source }
}

function Install-Python {
    Set-Status 'Téléchargement de Python et Tcl/Tk…'
    $download = Join-Path $work 'python-amd64.exe'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $client = New-Object Net.WebClient
    try {
        $task = $client.DownloadFileTaskAsync([uri]$pythonUrl, $download)
        $timer = [Diagnostics.Stopwatch]::StartNew()
        while (-not $task.IsCompleted) {
            if ($timer.Elapsed.TotalMinutes -gt 5) {
                $client.CancelAsync()
                throw 'Le téléchargement de Python a dépassé cinq minutes.'
            }
            [Windows.Forms.Application]::DoEvents()
            Start-Sleep -Milliseconds 100
        }
        $task.GetAwaiter().GetResult()
    } finally {
        $client.Dispose()
    }
    if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne $pythonSha256) {
        throw 'Le contrôle SHA-256 de Python a échoué.'
    }

    Set-Status 'Installation de Python et Tcl/Tk…'
    $pythonLog = Join-Path $env:LOCALAPPDATA 'SoundLive-NDI-python.log'
    $flags = '/quiet /norestart /log "{0}" InstallAllUsers=0 Include_launcher=0 PrependPath=0 Include_tcltk=1 Include_pip=1 Include_test=0 Include_doc=0' -f $pythonLog
    $result = Invoke-Hidden $download $flags
    if ($result.ExitCode -notin @(0, 3010)) {
        $hex = '0x{0:X8}' -f $result.ExitCode
        throw "L'installateur de Python a échoué (code $($result.ExitCode), $hex).`r`nJournal Python : $pythonLog"
    }
    if ($result.ExitCode -eq 3010) { Write-Log 'Python a demandé un redémarrage.' }
}

if (-not [Environment]::Is64BitProcess) {
    [void](Modal 'Ouvre cet installateur sur une version 64 bits de Windows.' 'OK' 'Error')
    exit 1
}

$versionFile = Join-Path $payload 'tableau.py'
if (-not (Test-Path -LiteralPath $versionFile -PathType Leaf)) {
    [void](Modal 'Décompresse le kit SoundLive-NDI complet avant de lancer cet installateur.' 'OK' 'Error')
    exit 1
}
$versionMatch = [regex]::Match((Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8),
    '(?m)^SOFTWARE_VERSION\s*=\s*"(\d+\.\d+\.\d+)"')
if (-not $versionMatch.Success) {
    [void](Modal 'La version de la régie est introuvable dans le kit.' 'OK' 'Error')
    exit 1
}
$version = $versionMatch.Groups[1].Value

if ((Modal "Installer la régie SoundLive-NDI $version pour ton compte Windows ?`r`n`r`n$installDir`r`n`r`nUne connexion Internet peut être nécessaire pour installer Python et les modules manquants." 'OKCancel' 'Question') -ne 'OK') { exit 0 }

$mutex = New-Object Threading.Mutex($false, 'Local\SoundLive-NDI-Regie-Setup')
$hasMutex = $false
$progress = $null
$work = $null
try {
    try { $hasMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $hasMutex = $true }
    if (-not $hasMutex) { throw 'Une installation de la régie est déjà en cours.' }
    Set-Content -LiteralPath $log -Value "Installation de la régie SoundLive-NDI $version" -Encoding UTF8
    $work = Join-Path ([IO.Path]::GetTempPath()) ('SoundLive-NDI-' + [guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $work)

    $progress = New-Object Windows.Forms.Form
    $progress.Text = $title
    $progress.StartPosition = 'CenterScreen'
    $progress.ClientSize = New-Object Drawing.Size(460, 98)
    $progress.FormBorderStyle = 'FixedDialog'
    $progress.ControlBox = $false
    $label = New-Object Windows.Forms.Label
    $label.SetBounds(18, 17, 420, 26)
    $bar = New-Object Windows.Forms.ProgressBar
    $bar.SetBounds(18, 57, 422, 12)
    $bar.Style = 'Marquee'
    $progress.Controls.Add($label)
    $progress.Controls.Add($bar)
    $progress.Show()

    Set-Status 'Vérification du dossier et du kit SoundLive-NDI…'
    $previous = if ($Depuis) { (Resolve-Path -LiteralPath $Depuis).Path } else { $payload }
    if (-not (Test-Path -LiteralPath $previous -PathType Container)) { throw 'Le dossier de reprise est introuvable.' }
    $installedTableau = Join-Path $installDir 'tableau.py'
    if (Test-Path -LiteralPath $installedTableau -PathType Leaf) {
        $existing = Get-Content -LiteralPath $installedTableau -Raw -Encoding UTF8
        if ($existing -match '(?m)^SOFTWARE_VERSION\s*=\s*"(\d+\.\d+\.\d+)"' -and
            [version]$Matches[1] -gt [version]$version) {
            throw "Une version plus récente ($($Matches[1])) est installée dans $installDir."
        }
        $active = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
            Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($installDir, [StringComparison]::OrdinalIgnoreCase) -ge 0 }
        if ($active) { throw 'Ferme le lanceur et arrête la régie avant de relancer cet installateur.' }
    }

    Set-Status 'Recherche d''une version compatible de Python…'
    $python = Find-Python
    if (-not $python) {
        Install-Python
        $python = Find-Python
        if (-not $python) { throw 'Python avec Tcl/Tk est introuvable après son installation.' }
    }
    Write-Log "Interpréteur : $python"
    $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'

    Set-Status 'Vérification des modules HTTPS et terminal SSH…'
    $check = '-c "import cryptography, paramiko, pyte"'
    $modules = Invoke-Hidden $python $check
    if ($modules.ExitCode -ne 0) {
        $pip = Invoke-Hidden $python '-m pip --version'
        if ($pip.ExitCode -ne 0) {
            $pip = Invoke-Hidden $python '-m ensurepip --user'
            if ($pip.ExitCode -ne 0) { throw 'Impossible de mettre en place pip pour installer les modules de la régie.' }
        }
        Set-Status 'Installation des modules HTTPS et terminal SSH…'
        $modules = Invoke-Hidden $python '-m pip --disable-pip-version-check --isolated install --user --no-input --timeout 20 --retries 2 --only-binary=:all: cryptography paramiko pyte truststore'
        if ($modules.ExitCode -ne 0) { throw 'Impossible de télécharger ou installer les modules Python. Vérifie la connexion Internet.' }
        $modules = Invoke-Hidden $python $check
        if ($modules.ExitCode -ne 0) { throw 'Les modules Python nécessaires sont introuvables après installation.' }
    }

    $env:SOUNDLIVE_SETUP_KIT = $payload
    $probeFiles = "import os,sys,pathlib; root=pathlib.Path(os.environ['SOUNDLIVE_SETUP_KIT']); sys.path.insert(0,str(root)); from versions_regie import FILES,required_files,software_version; v=software_version((root/'tableau.py').read_bytes(),'tableau.py'); a={f for f in FILES if (root/f).is_file()}; missing=required_files(v)-a; assert not missing, sorted(missing); print(*sorted(a),sep=chr(10))"
    $manifest = Invoke-Hidden $python "-B -c `"$probeFiles`""
    if ($manifest.ExitCode -ne 0 -or -not $manifest.Output) { throw 'Kit SoundLive-NDI incomplet ou illisible.' }
    $files = @($manifest.Output | ForEach-Object { Get-Item -LiteralPath (Join-Path $payload $_) })

    # Windows resolves the former directory regardless of case. Rename through a sibling
    # so File Explorer displays soundlive-ndi and the previous version keeps its data.
    if (Test-Path -LiteralPath $installDir -PathType Container) {
        $installParent = Split-Path -Parent $installDir
        $entry = Get-ChildItem -LiteralPath $installParent -Directory | Where-Object Name -ieq 'soundlive-ndi' | Select-Object -First 1
        if ($entry.Name -cne 'soundlive-ndi') {
            $oldName = $entry.Name
            $transit = 'soundlive-ndi-installation-' + [guid]::NewGuid().ToString('N')
            $intermediate = Join-Path $entry.Parent.FullName $transit
            Rename-Item -LiteralPath $entry.FullName -NewName $transit
            try {
                Rename-Item -LiteralPath $intermediate -NewName 'soundlive-ndi'
            } catch {
                Rename-Item -LiteralPath $intermediate -NewName $oldName
                throw
            }
        }
    }

    Set-Status 'Installation des fichiers de la régie…'
    [void](New-Item -ItemType Directory -Path $installDir, $maintenance -Force)
    $backup = Join-Path $work 'sauvegarde'
    [void](New-Item -ItemType Directory -Path $backup)
    $touched = New-Object 'Collections.Generic.List[string]'
    try {
        foreach ($file in $files) {
            $destination = Join-Path $installDir $file.Name
            if ($file.FullName -ieq $destination) { continue }
            if (Test-Path -LiteralPath $destination -PathType Container) {
                throw "Un dossier occupe déjà le chemin de $($file.Name)."
            }
            if (Test-Path -LiteralPath $destination -PathType Leaf) {
                Copy-Item -LiteralPath $destination -Destination $backup -Force
            }
            $touched.Add($file.Name)
            Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
        }
    } catch {
        foreach ($name in $touched) {
            $destination = Join-Path $installDir $name
            $old = Join-Path $backup $name
            try {
                if (Test-Path -LiteralPath $old -PathType Leaf) {
                    Copy-Item -LiteralPath $old -Destination $destination -Force
                } elseif (Test-Path -LiteralPath $destination -PathType Leaf) {
                    Remove-Item -LiteralPath $destination -Force
                }
            } catch { Write-Log "Restauration de $name impossible. $($_.Exception.Message)" }
        }
        throw
    }
    foreach ($name in @('ecrans.json', 'disposition-ecrans.json', 'regie-update.pub', 'source-publications.json',
                         'publication.pub', '.soundlive-update', '.soundlive-acces', '.soundlive-medias', '.soundlive-dhcp')) {
        $source = Join-Path $previous $name
        $destination = Join-Path $installDir $name
        if ((Test-Path -LiteralPath $source) -and -not (Test-Path -LiteralPath $destination)) {
            Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
        }
    }

    Set-Status 'Création des raccourcis et de la désinstallation…'
    $Launcher = @'
"""Start the installed régie with the interpreter selected by Setup."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

launcher = Path(__file__).resolve()
root = launcher.parent.parent
os.chdir(root)
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("soundlive_regie", root / "regie-windows.py")
regie = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = regie
spec.loader.exec_module(regie)


def restart_installed_regie(directory):
    subprocess.Popen([sys.executable, "-B", str(launcher)], cwd=directory)


regie.relaunch = restart_installed_regie
regie.main()
'@
    Set-Content -LiteralPath (Join-Path $maintenance 'lancer-regie.pyw') -Value $Launcher -Encoding UTF8
    $Uninstaller = @'
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
[Windows.Forms.Application]::EnableVisualStyles()
$title = 'Désinstaller SoundLive-NDI Régie'
$directory = Join-Path $env:LOCALAPPDATA 'Programs\soundlive-ndi'
$maintenance = Join-Path $directory '.installation'
$registry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SoundLive-NDI-Regie'
$consent = [Windows.Forms.MessageBox]::Show(
    'Supprimer les fichiers du programme et les raccourcis ? Les réglages et les associations seront conservés.',
    $title, 'YesNo', 'Question')
if ($consent -ne 'Yes') { exit 0 }

$mutex = New-Object Threading.Mutex($false, 'Local\SoundLive-NDI-Regie-Setup')
$held = $false
try {
    try { $held = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $held = $true }
    if (-not $held) { throw 'Une installation de la régie est déjà en cours.' }
    $processes = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($directory, [StringComparison]::OrdinalIgnoreCase) -ge 0 }
    if ($processes) { throw 'Ferme le lanceur et arrête la régie avant de la désinstaller.' }

    $manifest = Join-Path $maintenance 'fichiers.txt'
    $names = @(Get-Content -LiteralPath $manifest -Encoding UTF8)
    foreach ($name in $names) {
        if ($name -notmatch '^(?:\.gitignore|[a-zA-Z0-9][a-zA-Z0-9_.-]{0,98})$' -or $name.EndsWith('.')) {
            throw 'La liste des fichiers à désinstaller est invalide.'
        }
    }
    foreach ($name in $names) {
        $file = Join-Path $directory $name
        if (Test-Path -LiteralPath $file -PathType Leaf) { Remove-Item -LiteralPath $file -Force }
    }
    foreach ($target in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('DesktopDirectory'))) {
        if (-not $target) { continue }
        $shortcut = Join-Path $target 'SoundLive-NDI Régie.lnk'
        if (Test-Path -LiteralPath $shortcut -PathType Leaf) { Remove-Item -LiteralPath $shortcut -Force }
    }
    foreach ($name in @('lancer-regie.pyw', 'desinstaller.ps1', 'fichiers.txt')) {
        $file = Join-Path $maintenance $name
        if (Test-Path -LiteralPath $file -PathType Leaf) { Remove-Item -LiteralPath $file -Force }
    }
    foreach ($folder in @($maintenance, $directory)) {
        if ((Test-Path -LiteralPath $folder -PathType Container) -and
            -not @(Get-ChildItem -LiteralPath $folder -Force).Count) { [IO.Directory]::Delete($folder) }
    }
    if (Test-Path -LiteralPath $registry) { Remove-Item -LiteralPath $registry -Force }
    [void][Windows.Forms.MessageBox]::Show('La régie a été désinstallée.', $title, 'OK', 'Information')
} catch {
    [void][Windows.Forms.MessageBox]::Show($_.Exception.Message, $title, 'OK', 'Error')
    exit 1
} finally {
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
'@
    Set-Content -LiteralPath (Join-Path $maintenance 'desinstaller.ps1') -Value $Uninstaller -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $maintenance 'fichiers.txt') -Value $files.Name -Encoding UTF8
    $launcher = Join-Path $maintenance 'lancer-regie.pyw'
    $shell = New-Object -ComObject WScript.Shell
    foreach ($target in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('DesktopDirectory'))) {
        if (-not $target) { continue }
        $shortcut = $shell.CreateShortcut((Join-Path $target 'SoundLive-NDI Régie.lnk'))
        $shortcut.TargetPath = $pythonw
        $shortcut.Arguments = "-B `"$launcher`""
        $shortcut.WorkingDirectory = $installDir
        $shortcut.IconLocation = "$appExe,0"
        $shortcut.Description = 'Ouvrir le lanceur de la régie SoundLive-NDI'
        $shortcut.Save()
    }

    [void](New-Item -Path $registry -Force)
    $uninstallLauncher = @'
try {
    $ErrorActionPreference = 'Stop'
    $path = Join-Path $env:LOCALAPPDATA 'Programs\soundlive-ndi\.installation\desinstaller.ps1'
    $code = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
    & ([scriptblock]::Create($code))
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [void][Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Désinstallation SoundLive-NDI', 'OK', 'Error')
    exit 1
}
'@
    $uninstallEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($uninstallLauncher))
    $uninstallCommand = '"{0}" -NoLogo -NoProfile -NonInteractive -STA -WindowStyle Hidden -EncodedCommand {1}' -f `
        (Join-Path $PSHOME 'powershell.exe'), $uninstallEncoded
    $metadata = @{
        DisplayName = 'SoundLive-NDI Régie'
        DisplayVersion = $version
        DisplayIcon = "$appExe,0"
        Publisher = 'SoundLive'
        InstallLocation = $installDir
        UninstallString = $uninstallCommand
    }
    foreach ($key in $metadata.Keys) {
        [void](New-ItemProperty -Path $registry -Name $key -Value $metadata[$key] -PropertyType String -Force)
    }
    foreach ($key in @('NoModify', 'NoRepair')) {
        [void](New-ItemProperty -Path $registry -Name $key -Value 1 -PropertyType DWord -Force)
    }
    $accessError = ''
    Set-Status 'Préparation de TigerVNC, du bureau distant et du client SSH…'
    $helper = Join-Path $installDir 'installer-bureau-distant-windows.ps1'
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$helper`" -ActiverRdp -ClientSsh"
    try {
        if ($VncMsi) {
            $msi = (Resolve-Path -LiteralPath $VncMsi).Path
            $arguments += " -VncMsi `"$msi`""
        }
        $configuration = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') `
            -ArgumentList $arguments -Verb RunAs -Wait -PassThru
        if ($null -eq $configuration.ExitCode -or $configuration.ExitCode -ne 0) {
            throw "La configuration du bureau distant s’est terminée avec le code $($configuration.ExitCode)."
        }
    } catch {
        $accessError = $_.Exception.Message
        Write-Log $accessError
    }
    Set-Status 'Préparation des clés SSH et de mise à jour…'
    $setupKeys = Invoke-Hidden $python "-B `"$installedTableau`" --preparer-installations"
    if ($setupKeys.ExitCode -ne 0) {
        $accessError = ($accessError + "`r`nLa préparation des clés est à terminer.").Trim()
        Write-Log $accessError
    }
    Write-Log 'Installation de la régie terminée.'
    $progress.Hide()
    $message = "La régie $version est installée."
    if ($accessError) {
        $message += "`r`n`r`nAccès distants à terminer : $accessError`r`nJournal : $log"
    }
    if ((Modal "$message`r`n`r`nOuvrir le lanceur maintenant ?" 'YesNo' 'Information') -eq 'Yes') {
        Start-Process -FilePath $pythonw -ArgumentList "-B `"$launcher`"" -WorkingDirectory $installDir
    }
} catch {
    if ($progress) { $progress.Hide() }
    try { Write-Log $_.Exception.Message } catch {}
    [void](Modal "$($_.Exception.Message)`r`n`r`nJournal : $log" 'OK' 'Error')
    exit 1
} finally {
    if ($progress) { $progress.Dispose() }
    if ($work -and (Test-Path -LiteralPath $work)) {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($hasMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
