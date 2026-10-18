using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// Only explicit WSL installation may request UAC. Never change security policies.
class NovaWindow : Form {
    [DllImport("kernel32.dll")] static extern void GetNativeSystemInfo(out SYSTEM_INFO info);
    [StructLayout(LayoutKind.Sequential)] struct SYSTEM_INFO {
        public ushort architecture, reserved; public uint pageSize;
        public IntPtr minAddress, maxAddress, processorMask;
        public uint processors, processorType, granularity;
        public ushort level, revision;
    }
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
    readonly Dictionary<string, TextBox> fields = new Dictionary<string, TextBox>();
    readonly TextBox status = new TextBox { Multiline=true, ReadOnly=true, ScrollBars=ScrollBars.Vertical, Dock=DockStyle.Fill };
    readonly FlowLayoutPanel actions = new FlowLayoutPanel { Dock=DockStyle.Fill, AutoSize=true };
    readonly Button check = new Button { Text="1. Comprobar", AutoSize=true };
    readonly Button activate = new Button { Text="2. Vincular equipo", AutoSize=true, Enabled=false };
    readonly Button open = new Button { Text="Abrir NovaPOS", AutoSize=true };
    readonly Button stop = new Button { Text="Detener", AutoSize=true, Enabled=false };
    readonly Button engine = new Button { Text="Comprobar motor local", AutoSize=true };
    readonly Button wsl = new Button { Text="Preparar respaldo WSL2", AutoSize=true, Enabled=false };
    readonly Button installWsl = new Button { Text="Instalar WSL (administrador)", AutoSize=true, Visible=false };
    Process child;
    bool busy, closeWhenStopped;
    bool needsWsl, needsWslInstall;
    Dictionary<string, object> confirmed;
    Dictionary<string, object> checkedValues;

    [STAThread] static void Main(string[] args) {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        SYSTEM_INFO info; GetNativeSystemInfo(out info);
        if (Environment.OSVersion.Version.Major < 10 || info.architecture != 9 || !Environment.Is64BitProcess) {
            MessageBox.Show("NovaPOS requiere Windows 10/11 de 64 bits (x64). No se instalaron componentes.", "Equipo no compatible");
            return;
        }
        if (args.Length == 1 && args[0] == "--check-platform") return;
#if PACKAGE
        // Build verification can extract to a NEW disposable folder without running/configuring a DB.
        if (args.Length == 2 && args[0] == "--extract-only") { Extract(args[1]); return; }
#endif
        Application.Run(new NovaWindow());
    }

    NovaWindow() {
        Text="NovaPOS · Instalación y conexión";
        Font=new Font("Segoe UI", 10);
        AutoScaleMode=AutoScaleMode.Dpi;
        MinimumSize=new Size(650, 680); Size=new Size(820, 810);
        StartPosition=FormStartPosition.CenterScreen;
        var layout = new TableLayoutPanel { Dock=DockStyle.Fill, Padding=new Padding(24), ColumnCount=1, RowCount=4 };
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.Controls.Add(new Label {Text="NovaPOS · Tu aplicación online y offline", AutoSize=true, Font=new Font("Segoe UI",18,FontStyle.Bold), Margin=new Padding(0,0,0,16)});
        Controls.Add(layout);
#if PACKAGE
        var info = new Label { AutoSize=true, MaximumSize=new Size(720,0), Text="Instala la aplicación completa, Python y un PostgreSQL privado. Primero prueba el motor nativo; si Windows lo bloquea con el error 4551, ofrece preparar WSL2 con tu autorización.\r\n\r\nWSL2 requiere virtualización, descargas adicionales y puede pedir permisos de administrador y reiniciar. No se reinicia automáticamente.\r\n\r\nDespués debes vincular este computador con su propio usuario, caja y turno. No se copian credenciales ni ventas de otro equipo. No se reemplazan bases existentes ni se desactivan protecciones.\r\n\r\nDestino: " + InstallRoot() };
        layout.Controls.Add(info);
        var install = new Button { Text="Instalar NovaPOS", AutoSize=true, Margin=new Padding(0,20,0,20) };
        layout.Controls.Add(install); layout.Controls.Add(status);
        install.Click += async delegate {
            busy=true; install.Enabled=false;
            try {
                Log("Verificando y extrayendo componentes. Puede tardar varios minutos…");
                await Task.Run(() => Extract(InstallRoot()));
                Shortcuts(InstallRoot());
                Log("Instalación de archivos terminada. Se abrirá el asistente de vinculación.");
                Process.Start(new ProcessStartInfo(Path.Combine(InstallRoot(),"NovaPOS.exe")) {UseShellExecute=true});
            } catch (Exception) { Log("No se completó la instalación. Puede existir otra instalación, faltar espacio o haber un bloqueo de Windows. Los archivos se conservaron; no desactives las protecciones."); }
            finally { busy=false; install.Enabled=true; }
        };
#else
        var form = new TableLayoutPanel { AutoSize=true, Dock=DockStyle.Top, ColumnCount=3 };
        form.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize)); form.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
        AddField(form,"Servidor de la nube","host","");
        AddField(form,"Puerto de la nube","port","");
        AddField(form,"Base de datos","database","");
        AddField(form,"Usuario de conexión SQL","user","");
        AddField(form,"Contraseña SQL","password",""); fields["password"].UseSystemPasswordChar=true;
        AddField(form,"Certificado CA (.pem/.crt)","ca","");
        var browse = new Button {Text="Elegir…",AutoSize=true}; form.Controls.Add(browse,2,5);
        browse.Click += delegate { using (var dialog = new OpenFileDialog {Filter="Certificado CA|*.pem;*.crt|Todos|*.*"}) if(dialog.ShowDialog()==DialogResult.OK) fields["ca"].Text=dialog.FileName; };
        AddField(form,"Usuario NovaPOS (nombre)","pos_user","");
        AddField(form,"Caja (nombre)","point","");
        foreach(var field in fields.Values) field.TextChanged += delegate {confirmed=null; checkedValues=null; activate.Enabled=false;};
        layout.Controls.Add(form);
        actions.Controls.AddRange(new Control[]{engine,wsl,installWsl,check,activate,open,stop}); layout.Controls.Add(actions); layout.Controls.Add(status);
        Log("Solo llena los datos para la PRIMERA vinculación. Si ya está activado, pulsa Abrir NovaPOS.\r\nEl usuario debe tener un turno abierto online en esa caja. Las contraseñas se guardan cifradas para este usuario Windows.\r\nSe necesita internet para vincular. Después se usa la misma interfaz y el cambio offline es automático para las operaciones admitidas.");
        check.Click += delegate { StartWorker("check",Values()); };
        engine.Click += delegate { StartWorker("engine",new Dictionary<string,object>()); };
        wsl.Click += delegate {
            if(MessageBox.Show("Windows bloqueó el PostgreSQL nativo. ¿Preparar el respaldo WSL2?\r\n\r\nSe creará un Ubuntu exclusivo de NovaPOS, sin tocar tus otras distribuciones ni bases. Necesita internet, al menos 5 GB libres y virtualización. No se desactivará ninguna protección. Si falta WSL, habrá otra confirmación para instalarlo.","Autorizar respaldo WSL2",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return;
            StartWorker("wsl_prepare",new Dictionary<string,object>{{"wsl_consent",true}});
        };
        installWsl.Click += async delegate { await InstallWsl(); };
        activate.Click += delegate {
            if(confirmed==null || checkedValues==null) return;
            var message="Vincular exclusivamente a:\r\n" + confirmed["user_name"] + " · " + confirmed["point_name"] + "\r\nTurno " + confirmed["turn_id"] + "\r\n\r\nSe descargará una copia autorizada; no se modificarán datos de la nube. ¿Continuar?";
            if(MessageBox.Show(message,"Confirmar vinculación",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes) return;
            var values=new Dictionary<string,object>(checkedValues); values["confirmed"]=confirmed;
            StartWorker("activate", values);
        };
        open.Click += delegate { if(child!=null && !child.HasExited) OpenBrowser(); else StartWorker("serve",null); };
        stop.Click += delegate { StopWorker(); };
#endif
        FormClosing += delegate(object sender, FormClosingEventArgs e) {
            if(child!=null && !child.HasExited) {e.Cancel=true; closeWhenStopped=true; StopWorker();}
            else if(busy) {e.Cancel=true; Log("Espera a que termine la instalación/vinculación para cerrar.");}
        };
    }
    void AddField(TableLayoutPanel panel,string label,string key,string value) {
        int row=panel.RowCount++; var input=new TextBox {Text=value,Dock=DockStyle.Fill,Margin=new Padding(8,4,4,4)};
        fields[key]=input; panel.Controls.Add(new Label {Text=label,AutoSize=true,Anchor=AnchorStyles.Left,Margin=new Padding(0,7,0,7)},0,row); panel.Controls.Add(input,1,row);
    }
    Dictionary<string,object> Values() {var result=new Dictionary<string,object>(); foreach(var pair in fields)result[pair.Key]=pair.Value.Text; return result;}
    void Log(string text) { if(IsDisposed) return; if(InvokeRequired) {BeginInvoke(new Action<string>(Log),text); return;} status.AppendText(text+Environment.NewLine+Environment.NewLine); }
    void OpenBrowser() {Process.Start(new ProcessStartInfo("http://127.0.0.1:8000/") {UseShellExecute=true});}
    void SetAvailable(bool available) {
        check.Enabled=available; open.Enabled=available; engine.Enabled=available;
        wsl.Enabled=available && needsWsl; installWsl.Enabled=available && needsWslInstall;
        installWsl.Visible=needsWslInstall; activate.Enabled=available && confirmed!=null;
        foreach(var field in fields.Values)field.Enabled=available;
    }
    async Task InstallWsl() {
        if(busy)return;
        if(MessageBox.Show("¿Autorizar la instalación oficial de WSL? Windows puede pedir permisos de administrador y un reinicio.\r\n\r\nGuarda tu trabajo. NovaPOS no reiniciará el equipo, no cambiará el firewall ni desactivará protecciones. Después vuelve a abrir NovaPOS y pulsa Preparar respaldo WSL2.","Instalar WSL",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return;
        busy=true; SetAvailable(false);
        try {
            var start=new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"wsl.exe"),"--install --no-distribution") {UseShellExecute=true,Verb="runas",WindowStyle=ProcessWindowStyle.Hidden};
            using(var process=Process.Start(start)) {
                Log("Se solicitó instalar WSL a Windows. Acepta el permiso solo si deseas continuar. Puede tardar varios minutos…");
                await Task.Run(()=>process.WaitForExit());
                Log("El instalador de WSL terminó con código "+process.ExitCode+". Si Windows requiere reiniciar, hazlo antes de continuar. Después pulsa Preparar respaldo WSL2; se verificará el estado real.");
            }
        } catch {Log("No se completó la instalación de WSL o cancelaste el permiso. No se creó una base ni se modificaron protecciones. Revisa virtualización y requisitos de Windows.");}
        finally {busy=false;SetAvailable(true);}
    }
    void StartWorker(string action, Dictionary<string,object> values) {
        if(busy || (child!=null && !child.HasExited))return;
        busy=true; SetAvailable(false);
        var start=new ProcessStartInfo(Path.Combine(root,"python","python.exe"),"-B \""+Path.Combine(root,"app","windows_installer","worker.py")+"\" "+action) {
            UseShellExecute=false, CreateNoWindow=true, WindowStyle=ProcessWindowStyle.Hidden, WorkingDirectory=Path.Combine(root,"app"),
            RedirectStandardInput=true, RedirectStandardOutput=true, RedirectStandardError=true, StandardOutputEncoding=Encoding.UTF8, StandardErrorEncoding=Encoding.UTF8
        };
        foreach(string key in new string[]{"PYTHONHOME","PYTHONPATH","DJANGO_SETTINGS_MODULE","NOVA_INTEGRATED_CONFIG","NOVA_LOCAL_CONFIG","RUN_MAIN","DATABASE_URL"})start.EnvironmentVariables.Remove(key);
        start.EnvironmentVariables["PYTHONUTF8"]="1";
        try {
            child=new Process {StartInfo=start, EnableRaisingEvents=true};
            child.OutputDataReceived += delegate(object sender,DataReceivedEventArgs e) {
                if(e.Data==null || !e.Data.StartsWith("{"))return;
                try {
                    var data=json.Deserialize<Dictionary<string,object>>(e.Data);
                    if(data.ContainsKey("message"))Log((string)data["message"]);
                    BeginInvoke(new Action(delegate {
                        if(data.ContainsKey("confirmed")) {confirmed=(Dictionary<string,object>)data["confirmed"]; checkedValues=values; Log("Usuario: "+confirmed["user_name"]+" · Caja: "+confirmed["point_name"]+" · Turno: "+confirmed["turn_id"]);}
                        if(data.ContainsKey("ready"))OpenBrowser();
                        if(data.ContainsKey("wsl_required")) {needsWsl=true;Log("Pulsa Preparar respaldo WSL2 para autorizarlo. Puedes cerrar y continuar después.");}
                        if(data.ContainsKey("wsl_install_required")) {needsWsl=true;needsWslInstall=true;installWsl.Visible=true;}
                        if(data.ContainsKey("engine_ready")) {needsWsl=false;needsWslInstall=false;installWsl.Visible=false;}
                    }));
                } catch { /* Only the structured status protocol is shown, never SQL/tracebacks. */ }
            };
            child.ErrorDataReceived += delegate { /* Deliberately do not persist raw credential-bearing exceptions. */ };
            child.Exited += delegate {
                var finished=child;
                Task.Run(delegate {
                    // Drain final structured output before enabling follow-up
                    // buttons; Exited can otherwise race OutputDataReceived.
                    finished.WaitForExit();
                    BeginInvoke(new Action(delegate {
                        busy=false; stop.Enabled=false; SetAvailable(true);
                        if(finished.ExitCode!=0)Log("El proceso no terminó correctamente. Conserva los datos. No desactives Smart App Control ni Defender.");
                        if(closeWhenStopped)Close();
                    }));
                });
            };
            child.Start(); child.BeginOutputReadLine(); child.BeginErrorReadLine();
            if(values!=null) {child.StandardInput.WriteLine(json.Serialize(values));child.StandardInput.Close();}
            stop.Enabled=action=="serve";
        } catch {child=null;busy=false;SetAvailable(true);Log("Windows no pudo iniciar Python. Revisa el bloqueo o las dependencias; no desactives las protecciones.");}
    }
    void StopWorker() {
        if(child==null || child.HasExited)return;
        if(!stop.Enabled) {Log("La vinculación sigue en curso. Espera a que termine; no se interrumpirá la copia."); closeWhenStopped=false;return;}
        stop.Enabled=false; Log("Deteniendo la aplicación y PostgreSQL de forma segura…");
        try {child.StandardInput.WriteLine("STOP");child.StandardInput.Flush();}catch {Log("No se pudo enviar la señal de cierre. Revisa el proceso antes de apagar el equipo.");}
    }
#if PACKAGE
    static string InstallRoot() {return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Programs","NovaPOS");}
    static void Extract(string target) {
        target=Path.GetFullPath(target);
        if(Directory.Exists(target) || File.Exists(target))throw new IOException("Ya existe una instalación; no se reemplaza.");
        var drive=new DriveInfo(Path.GetPathRoot(target)); if(drive.AvailableFreeSpace < 3L*1024*1024*1024)throw new IOException("Falta espacio.");
        using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("NovaPayload")) {
            using(var sha=SHA256.Create()) {var hash=BitConverter.ToString(sha.ComputeHash(stream)).Replace("-","").ToLowerInvariant(); if(hash!=Payload.Sha256)throw new IOException("Paquete alterado.");}
            stream.Position=0;
            using(var archive=new ZipArchive(stream,ZipArchiveMode.Read)) {
                Directory.CreateDirectory(target); File.WriteAllText(Path.Combine(target,".installing"),"Instalación incompleta: no elimines carpetas con datos.");
                foreach(var item in archive.Entries) {
                    var path=Path.GetFullPath(Path.Combine(target,item.FullName.Replace('/',Path.DirectorySeparatorChar)));
                    if(!path.StartsWith(target+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase) || item.FullName.Contains(":"))throw new IOException("Ruta inválida.");
                    Directory.CreateDirectory(Path.GetDirectoryName(path));
                    using(var input=item.Open())using(var output=new FileStream(path,FileMode.CreateNew))input.CopyTo(output);
                }
                File.Delete(Path.Combine(target,".installing"));
            }
        }
    }
    static void Shortcuts(string target) {
        var type=Type.GetTypeFromProgID("WScript.Shell"); dynamic shell=Activator.CreateInstance(type);
        foreach(var folder in new string[]{Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),Environment.GetFolderPath(Environment.SpecialFolder.StartMenu)}) {
            dynamic link=shell.CreateShortcut(Path.Combine(folder,"NovaPOS.lnk")); link.TargetPath=Path.Combine(target,"NovaPOS.exe");link.WorkingDirectory=target;link.Description="NovaPOS online y offline";link.Save();
        }
    }
#endif
}
