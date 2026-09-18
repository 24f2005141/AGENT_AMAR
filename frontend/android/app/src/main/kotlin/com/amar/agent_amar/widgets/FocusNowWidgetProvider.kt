package com.amar.agent_amar.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.view.View
import android.widget.RemoteViews
import com.amar.agent_amar.R
import es.antonborri.home_widget.HomeWidgetBackgroundIntent
import es.antonborri.home_widget.HomeWidgetProvider
import android.content.SharedPreferences
import android.net.Uri

/**
 * FOCUS NOW — the single highest-priority unresolved item.
 *
 * The item was already selected in Dart (WidgetSnapshotBuilder) using the same
 * active-item rules as the homepage, so completed / resolved / cleared /
 * snoozed / spam items can never reach this widget.
 */
class FocusNowWidgetProvider : HomeWidgetProvider() {

    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences
    ) {
        val snapshot = SortedWidgetData.snapshot(context)
        val focus = SortedWidgetData.optObject(snapshot, "focus")

        appWidgetIds.forEach { widgetId ->
            val views = RemoteViews(context.packageName, R.layout.widget_focus_now).apply {
                if (focus == null) {
                    // "✓ You're Sorted — nothing needs your attention."
                    setViewVisibility(R.id.focus_title, View.GONE)
                    setViewVisibility(R.id.focus_subtitle, View.GONE)
                    setViewVisibility(R.id.focus_deadline, View.GONE)
                    setViewVisibility(R.id.focus_actions, View.GONE)
                    setViewVisibility(R.id.focus_empty, View.VISIBLE)
                } else {
                    val emailId = focus.optString("email_id")
                    setViewVisibility(R.id.focus_empty, View.GONE)
                    setViewVisibility(R.id.focus_title, View.VISIBLE)
                    setViewVisibility(R.id.focus_subtitle, View.VISIBLE)
                    setViewVisibility(R.id.focus_actions, View.VISIBLE)

                    val critical = focus.optBoolean("is_critical", false)
                    val prefix = if (critical) "⚠ " else ""
                    setTextViewText(R.id.focus_title, prefix + focus.optString("title"))
                    setTextViewText(R.id.focus_subtitle, focus.optString("subtitle"))

                    val deadline = SortedWidgetData.parseUtc(focus.optString("deadline", null))
                    if (deadline == null) {
                        setViewVisibility(R.id.focus_deadline, View.GONE)
                    } else {
                        setViewVisibility(R.id.focus_deadline, View.VISIBLE)
                        val overdue = deadline.time < System.currentTimeMillis()
                        val label = if (overdue) "Overdue · " else "Deadline: "
                        setTextViewText(
                            R.id.focus_deadline,
                            label + SortedWidgetData.formatWhen(deadline)
                        )
                    }

                    // View → deep-link into this email in the Flutter app.
                    val view = SortedWidgetData.launchIntent(context, "email", "id=$emailId")
                    setOnClickPendingIntent(R.id.focus_title, view)
                    setOnClickPendingIntent(R.id.focus_view_button, view)

                    // Done → background callback into Dart (no backend call from
                    // the widget: the completion is queued locally and flushed
                    // by the app, which is authoritative).
                    val done = HomeWidgetBackgroundIntent.getBroadcast(
                        context,
                        Uri.parse("agentamar://widget/done?id=$emailId")
                    )
                    setOnClickPendingIntent(R.id.focus_done_button, done)
                }

                // Tapping the card itself always opens Sorted.
                setOnClickPendingIntent(
                    R.id.focus_root,
                    SortedWidgetData.launchIntent(context, "tab", "name=inbox")
                )
            }
            appWidgetManager.updateAppWidget(widgetId, views)
        }
    }
}
