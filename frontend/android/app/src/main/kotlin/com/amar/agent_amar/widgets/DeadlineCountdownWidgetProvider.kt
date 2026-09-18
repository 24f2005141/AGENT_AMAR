package com.amar.agent_amar.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.os.SystemClock
import android.view.View
import android.widget.RemoteViews
import com.amar.agent_amar.R
import es.antonborri.home_widget.HomeWidgetProvider

/**
 * DEADLINE COUNTDOWN — time left until the nearest upcoming deadline.
 *
 * The ticking is done by the platform `Chronometer` counting down against the
 * device clock, so the countdown stays live without the app running and
 * WITHOUT any periodic widget update — no wakelocks, no polling, no network.
 * An expired or completed deadline is filtered out in Dart before it ever
 * gets here, and if the moment passes while the widget is on screen the
 * Chronometer simply counts past zero until the next real update.
 */
class DeadlineCountdownWidgetProvider : HomeWidgetProvider() {

    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences
    ) {
        val snapshot = SortedWidgetData.snapshot(context)
        val next = SortedWidgetData.optObject(snapshot, "next_deadline")
        val deadlineAt = SortedWidgetData.parseUtc(next?.optString("deadline_at", null))
        val hasDeadline = next != null && deadlineAt != null &&
            deadlineAt.time > System.currentTimeMillis()

        appWidgetIds.forEach { widgetId ->
            val views = RemoteViews(context.packageName, R.layout.widget_deadline_countdown).apply {
                if (!hasDeadline) {
                    setViewVisibility(R.id.deadline_title, View.GONE)
                    setViewVisibility(R.id.deadline_chronometer, View.GONE)
                    setViewVisibility(R.id.deadline_when, View.GONE)
                    setViewVisibility(R.id.deadline_empty, View.VISIBLE)
                } else {
                    setViewVisibility(R.id.deadline_empty, View.GONE)
                    setViewVisibility(R.id.deadline_title, View.VISIBLE)
                    setViewVisibility(R.id.deadline_chronometer, View.VISIBLE)
                    setViewVisibility(R.id.deadline_when, View.VISIBLE)

                    setTextViewText(R.id.deadline_title, next!!.optString("title"))
                    setTextViewText(R.id.deadline_when, SortedWidgetData.formatWhen(deadlineAt!!))

                    // Chronometer base is on the elapsed-realtime clock.
                    val deltaMs = deadlineAt.time - System.currentTimeMillis()
                    setChronometer(
                        R.id.deadline_chronometer,
                        SystemClock.elapsedRealtime() + deltaMs,
                        null,
                        true
                    )
                    setChronometerCountDown(R.id.deadline_chronometer, true)

                    setOnClickPendingIntent(
                        R.id.deadline_root,
                        SortedWidgetData.launchIntent(
                            context, "email", "id=" + next.optString("email_id")
                        )
                    )
                }

                if (!hasDeadline) {
                    setOnClickPendingIntent(
                        R.id.deadline_root,
                        SortedWidgetData.launchIntent(context, "tab", "name=deadlines")
                    )
                }
            }
            appWidgetManager.updateAppWidget(widgetId, views)
        }
    }
}
