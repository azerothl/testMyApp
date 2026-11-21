using System;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Threading;
using System.Windows.Forms;
using Timer = System.Windows.Forms.Timer;

static class RecetteWindows
{
    [STAThread]
    static int Main(string[] args)
    {
        string visualTemplate = null;
        string startupLog = null;
        int startupDelayMs = 0;
        for (int index = 0; index < args.Length; index += 2)
        {
            if (index + 1 == args.Length)
                throw new ArgumentException("Valeur manquante pour " + args[index]);
            string value = args[index + 1];
            switch (args[index].ToLowerInvariant())
            {
                case "-visualtemplate": visualTemplate = value; break;
                case "-startuplog": startupLog = value; break;
                case "-startupdelayms": startupDelayMs = int.Parse(value, CultureInfo.InvariantCulture); break;
                default: throw new ArgumentException("Option inconnue : " + args[index]);
            }
        }

        if (!string.IsNullOrEmpty(startupLog))
        {
            using (var process = Process.GetCurrentProcess())
                File.AppendAllText(startupLog, process.Id.ToString(CultureInfo.InvariantCulture) + Environment.NewLine);
        }
        if (startupDelayMs > 0)
            Thread.Sleep(startupDelayMs);

        Application.EnableVisualStyles();
        using (var glyph = string.IsNullOrEmpty(visualTemplate) ? null : Image.FromFile(visualTemplate))
        using (var form = new Form())
        using (var freezeTimer = new Timer())
        using (var exitTimer = new Timer())
        {
            form.Text = "TestMyApp - Recette Windows";
            form.StartPosition = FormStartPosition.CenterScreen;
            form.ClientSize = new Size(390, glyph == null ? 190 : 285);
            var layout = new FlowLayoutPanel
            {
                FlowDirection = FlowDirection.TopDown,
                WrapContents = false,
                Padding = new Padding(14),
                Dock = DockStyle.Fill
            };
            form.Controls.Add(layout);
            var label = new Label { AutoSize = true, Text = "Items : 0" };
            layout.Controls.Add(label);

            int items = 0;
            var add = new Button { Text = "Ajouter un item", AutoSize = true };
            add.Click += (_, eventArgs) => label.Text = "Items : " + ++items;
            layout.Controls.Add(add);

            freezeTimer.Interval = 100;
            freezeTimer.Tick += (_, eventArgs) =>
            {
                freezeTimer.Stop();
                Thread.Sleep(2400);
                label.Text = "Fenetre reactive";
            };
            var freeze = new Button { Text = "Simuler un gel", AutoSize = true };
            freeze.Click += (_, eventArgs) => freezeTimer.Start();
            layout.Controls.Add(freeze);

            int exitCode = 0;
            exitTimer.Interval = 120;
            exitTimer.Tick += (_, eventArgs) =>
            {
                exitTimer.Stop();
                exitCode = 23;
                form.Close();
            };
            var exit = new Button { Text = "Simuler un crash", AutoSize = true };
            exit.Click += (_, eventArgs) => exitTimer.Start();
            layout.Controls.Add(exit);

            if (glyph != null)
                AddVisualControl(layout, glyph, label);

            Application.Run(form);
            return exitCode;
        }
    }

    static void AddVisualControl(FlowLayoutPanel layout, Image glyph, Label label)
    {
        var bounds = new Rectangle(26, 9, glyph.Width, glyph.Height);
        var surface = new Panel { Size = new Size(210, 80) };
        surface.Paint += (_, eventArgs) => eventArgs.Graphics.DrawImageUnscaled(glyph, bounds.Location);
        surface.MouseClick += (_, eventArgs) =>
        {
            if (bounds.Contains(eventArgs.Location))
                label.Text = "Clic image recu";
        };
        layout.Controls.Add(surface);
    }
}
