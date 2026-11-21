$ErrorActionPreference = 'Stop'

$sessionId = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
if ($sessionId -eq 0 -or -not [Environment]::UserInteractive) {
    throw "Les recettes nécessitent une session Windows interactive."
}

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class DesktopRegressionSession
{
    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool CloseDesktop(IntPtr desktop);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool GetUserObjectInformation(IntPtr handle, int index,
        StringBuilder value, uint size, out uint required);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr GetThreadDesktop(uint threadId);

    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();

    public static bool HasActiveDesktop()
    {
        // DESKTOP_READOBJECTS and UOI_NAME.
        IntPtr input = OpenInputDesktop(0, false, 1);
        if (input == IntPtr.Zero)
            return false;
        try
        {
            return IsDefault(input) && IsDefault(GetThreadDesktop(GetCurrentThreadId()));
        }
        finally
        {
            CloseDesktop(input);
        }
    }

    private static bool IsDefault(IntPtr desktop)
    {
        var name = new StringBuilder(256);
        return desktop != IntPtr.Zero &&
            GetUserObjectInformation(desktop, 2, name, (uint)(name.Capacity * 2), out _) &&
            String.Equals(name.ToString(), "Default", StringComparison.OrdinalIgnoreCase);
    }
}
'@

if (-not [DesktopRegressionSession]::HasActiveDesktop()) {
    throw "Le bureau Windows doit être actif et déverrouillé pour les recettes natives."
}

Write-Host "Bureau interactif actif dans la session Windows $sessionId."
$env:TESTMYAPP_DESKTOP = '1'
python -B -m unittest discover -s tests -p test_integration.py -k DesktopIntegration -v
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
