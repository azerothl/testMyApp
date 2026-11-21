param(
    [string]$VisualTemplate,
    [int]$StartupDelayMs = 0,
    [string]$StartupLog
)

if ($StartupLog) {
    [System.IO.File]::AppendAllText($StartupLog, "$PID`r`n")
}
if ($StartupDelayMs -gt 0) {
    Start-Sleep -Milliseconds $StartupDelayMs
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = [System.Windows.Forms.Form]::new()
$form.Text = 'TestMyApp - Recette Windows'
$form.StartPosition = 'CenterScreen'
$form.ClientSize = [System.Drawing.Size]::new(390, 190)

$layout = [System.Windows.Forms.FlowLayoutPanel]::new()
$layout.FlowDirection = 'TopDown'
$layout.WrapContents = $false
$layout.Padding = [System.Windows.Forms.Padding]::new(14)
$layout.Dock = 'Fill'
$form.Controls.Add($layout)

$label = [System.Windows.Forms.Label]::new()
$label.AutoSize = $true
$label.Text = 'Items : 0'
$layout.Controls.Add($label)

$addButton = [System.Windows.Forms.Button]::new()
$addButton.Text = 'Ajouter un item'
$addButton.AutoSize = $true
$script:items = 0
$addButton.Add_Click({
    $script:items++
    $label.Text = "Items : $script:items"
})
$layout.Controls.Add($addButton)

$freezeTimer = [System.Windows.Forms.Timer]::new()
$freezeTimer.Interval = 100
$freezeTimer.Add_Tick({
    $freezeTimer.Stop()
    [System.Threading.Thread]::Sleep(2400)
    $label.Text = 'Fenetre reactive'
})
$freezeButton = [System.Windows.Forms.Button]::new()
$freezeButton.Text = 'Simuler un gel'
$freezeButton.AutoSize = $true
$freezeButton.Add_Click({ $freezeTimer.Start() })
$layout.Controls.Add($freezeButton)

$crashTimer = [System.Windows.Forms.Timer]::new()
$crashTimer.Interval = 120
$crashTimer.Add_Tick({
    $crashTimer.Stop()
    $form.Tag = 'crash'
    $form.Close()
})
$crashButton = [System.Windows.Forms.Button]::new()
$crashButton.Text = 'Simuler un crash'
$crashButton.AutoSize = $true
$crashButton.Add_Click({ $crashTimer.Start() })
$layout.Controls.Add($crashButton)

if ($VisualTemplate) {
    $form.ClientSize = [System.Drawing.Size]::new(390, 285)
    $glyph = [System.Drawing.Image]::FromFile($VisualTemplate)
    $surface = [System.Windows.Forms.Panel]::new()
    $surface.Size = [System.Drawing.Size]::new(210, 80)
    $surface.Add_Paint({ param($sender, $eventArgs) $eventArgs.Graphics.DrawImageUnscaled($glyph, 26, 9) })
    $surface.Add_MouseClick({
        param($sender, $eventArgs)
        if ($eventArgs.X -ge 26 -and $eventArgs.X -lt (26 + $glyph.Width) -and
            $eventArgs.Y -ge 9 -and $eventArgs.Y -lt (9 + $glyph.Height)) {
            $label.Text = 'Clic image recu'
        }
    })
    $layout.Controls.Add($surface)
}

[System.Windows.Forms.Application]::Run($form)
if ($form.Tag -eq 'crash') {
    exit 23
}
