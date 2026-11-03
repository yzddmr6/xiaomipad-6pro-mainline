/* SPDX-License-Identifier: GPL-3.0-or-later
 * Narrow native GTK extension for the existing vendor Settings binary.
 * Uses public GTK/GIO APIs; no private struct offsets or authentication hooks.
 * Only loaded by the Settings launcher, only modifies CcFingerprintDialog.
 */
#define _GNU_SOURCE
#include <gio/gio.h>
#include <dlfcn.h>
/* Opaque public GTK ABI types; installed runtime .so is the link target. */
typedef struct _GtkWidget GtkWidget;
extern GObject *gtk_widget_get_template_child(GtkWidget *, GType, const char *);
extern GtkWidget *gtk_widget_get_parent(GtkWidget *);
extern gboolean gtk_widget_get_mapped(GtkWidget *);
extern GtkWidget *gtk_label_new(const char *);
extern void gtk_label_set_text(void *, const char *);
extern void gtk_label_set_wrap(void *, gboolean);
extern void gtk_widget_add_css_class(GtkWidget *, const char *);
extern void gtk_box_insert_child_after(void *, GtkWidget *, GtkWidget *);
extern void adw_dialog_close(void *);

typedef struct {
    GtkWidget *dialog, *view, *label;
    GDBusProxy *proxy;
    guint accepted, total, timer;
    gboolean finished, was_mapped;
} Progress;
static Progress *active;
static guint signal_subscription;
static GDBusConnection *session;

/* The extension belongs to Settings only, never its helper subprocesses. */
__attribute__((constructor)) static void stop_preload_inheritance(void)
{
    g_unsetenv("LD_PRELOAD");
}

static gboolean update(gpointer data)
{
    Progress *p=data;
    gboolean mapped=gtk_widget_get_mapped(p->view);
    if (mapped && !p->was_mapped) {
        p->accepted=0; p->finished=FALSE;
    }
    p->was_mapped=mapped;
    if (!mapped) return G_SOURCE_CONTINUE;
    const char *phase="准备采集";
    if (p->proxy) {
        g_autoptr(GVariant) total=g_dbus_proxy_get_cached_property(p->proxy,"num-enroll-stages");
        if (total && g_variant_is_of_type(total,G_VARIANT_TYPE_INT32) && g_variant_get_int32(total)>0)
            p->total=(guint)g_variant_get_int32(total);
        g_autoptr(GVariant) present=g_dbus_proxy_get_cached_property(p->proxy,"finger-present");
        g_autoptr(GVariant) needed=g_dbus_proxy_get_cached_property(p->proxy,"finger-needed");
        if (present && g_variant_get_boolean(present)) phase="请完全抬起手指";
        else if (needed && g_variant_get_boolean(needed)) phase="请按下手指";
    }
    if (p->finished) phase="已完成";
    g_autofree char *text=g_strdup_printf("已录入 %u 次 / 共 %u 次 · %s", p->accepted,p->total,phase);
    gtk_label_set_text(p->label,text);
    return G_SOURCE_CONTINUE;
}

static gboolean dbus_hook(GSignalInvocationHint *hint, guint n, const GValue *values, gpointer data)
{
    (void)hint; (void)data;
    if (!active || n<4) return TRUE;
    GDBusProxy *proxy=g_value_get_object(&values[0]);
    if (g_strcmp0(g_dbus_proxy_get_interface_name(proxy),"net.reactivated.Fprint.Device")) return TRUE;
    const char *name=g_value_get_string(&values[2]);
    if (g_strcmp0(name,"EnrollStatus")) return TRUE;
    GVariant *params=g_value_get_variant(&values[3]);
    if (!params || !g_variant_is_of_type(params,G_VARIANT_TYPE("(sb)"))) return TRUE;
    g_set_object(&active->proxy,proxy);
    const char *status; gboolean done;
    g_variant_get(params,"(&sb)",&status,&done);
    if (!g_strcmp0(status,"enroll-stage-passed")) active->accepted++;
    if (!g_strcmp0(status,"enroll-completed")) {
        active->accepted=active->total; active->finished=TRUE;
    }
    g_message("Fingerprint counter: %u/%u status=%s done=%d",active->accepted,active->total,status,done);
    return TRUE;
}

static void lock_changed(GDBusConnection *c,const char *sender,const char *path,
                         const char *interface,const char *signal,GVariant *params,gpointer data)
{
    (void)c;(void)sender;(void)path;(void)interface;(void)signal;(void)data;
    gboolean locked=FALSE;
    if (g_variant_is_of_type(params,G_VARIANT_TYPE("(b)"))) g_variant_get(params,"(b)",&locked);
    if (locked && active) {
        g_message("Fingerprint settings: close dialog on lock to release reader");
        adw_dialog_close(active->dialog);
    }
}

static void free_progress(gpointer data)
{
    Progress *p=data;
    if (active==p) active=NULL;
    if (p->timer) g_source_remove(p->timer);
    g_clear_object(&p->proxy);
    g_free(p);
}

void gtk_widget_init_template(GtkWidget *widget)
{
    static void (*original)(GtkWidget *);
    if (!original) original=dlsym(RTLD_NEXT,"gtk_widget_init_template");
    original(widget);
    if (g_strcmp0(G_OBJECT_TYPE_NAME(widget),"CcFingerprintDialog")) return;
    GType type=G_OBJECT_TYPE(widget);
    GtkWidget *message=(GtkWidget *)gtk_widget_get_template_child(widget,type,"enroll_message");
    GtkWidget *view=(GtkWidget *)gtk_widget_get_template_child(widget,type,"enrollment_view");
    if (!message || !view) return;
    GtkWidget *parent=gtk_widget_get_parent(message);
    if (!parent || !g_type_check_instance_is_a((GTypeInstance *)parent,g_type_from_name("GtkBox"))) return;
    static gboolean hooked;
    if (!hooked) {
        g_type_class_ref(G_TYPE_DBUS_PROXY);
        guint id=g_signal_lookup("g-signal",G_TYPE_DBUS_PROXY);
        if (id) g_signal_add_emission_hook(id,0,dbus_hook,NULL,NULL);
        hooked=TRUE;
    }
    Progress *p=g_new0(Progress,1);
    p->dialog=widget; p->view=view; p->total=26;
    p->label=gtk_label_new("已录入 0 次 / 共 26 次 · 准备采集");
    gtk_label_set_wrap(p->label,TRUE);
    gtk_widget_add_css_class(p->label,"heading");
    gtk_box_insert_child_after(parent,p->label,message);
    g_object_set_data_full(G_OBJECT(widget),"liuqin-fingerprint-progress",p,free_progress);
    active=p;
    p->timer=g_timeout_add(150,update,p);
    if (!session) session=g_bus_get_sync(G_BUS_TYPE_SESSION,NULL,NULL);
    if (session && !signal_subscription)
        signal_subscription=g_dbus_connection_signal_subscribe(session,"org.gnome.ScreenSaver",
          "org.gnome.ScreenSaver","ActiveChanged","/org/gnome/ScreenSaver",NULL,
          G_DBUS_SIGNAL_FLAGS_NONE,lock_changed,NULL,NULL);
    g_message("Fingerprint progress UI installed (native Settings dialog)");
}
