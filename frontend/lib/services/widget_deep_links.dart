/// Parsing for the home-screen widget deep links.
///
/// One scheme for the whole app (`agentamar://`), routed on host: `auth` is
/// the OAuth callback (see `ApiConfig.isAuthCallback`), `widget` is a tap on a
/// home-screen widget. Kept as pure parsing so routing stays testable and the
/// widgets never own navigation logic of their own.
library;

enum WidgetRouteKind { email, tab, done, unknown }

/// Which Sorted view a widget tap asked for. Maps onto the EXISTING bottom-nav
/// tabs — no new navigation architecture.
enum WidgetTab {
  inbox,
  actions,
  replies,
  deadlines;

  /// Index in `MainNavigationScreen`'s IndexedStack.
  int get tabIndex {
    switch (this) {
      case WidgetTab.inbox:
        return 0;
      case WidgetTab.actions:
        return 1;
      case WidgetTab.deadlines:
        return 2;
      // Replies live on the home feed's "Reply Needed" filter, not a tab.
      case WidgetTab.replies:
        return 0;
    }
  }

  /// The home-feed filter chip to apply, if any.
  String? get inboxFilter {
    switch (this) {
      case WidgetTab.replies:
        return 'reply_needed';
      case WidgetTab.inbox:
        return 'all';
      case WidgetTab.actions:
      case WidgetTab.deadlines:
        return null;
    }
  }

  static WidgetTab? fromName(String? name) {
    switch (name) {
      case 'inbox':
        return WidgetTab.inbox;
      case 'actions':
        return WidgetTab.actions;
      case 'replies':
        return WidgetTab.replies;
      case 'deadlines':
        return WidgetTab.deadlines;
      default:
        return null;
    }
  }
}

class WidgetRoute {
  final WidgetRouteKind kind;
  final String? emailId;
  final WidgetTab? tab;

  const WidgetRoute(this.kind, {this.emailId, this.tab});

  static const WidgetRoute none = WidgetRoute(WidgetRouteKind.unknown);

  /// `agentamar://widget/email?id=…` | `…/tab?name=…` | `…/done?id=…`
  static WidgetRoute parse(Uri uri) {
    if (uri.scheme != 'agentamar' || uri.host != 'widget') return none;
    final segments = uri.pathSegments;
    if (segments.isEmpty) return none;
    switch (segments.first) {
      case 'email':
        final id = uri.queryParameters['id'];
        if (id == null || id.isEmpty) return none;
        return WidgetRoute(WidgetRouteKind.email, emailId: id);
      case 'tab':
        final tab = WidgetTab.fromName(uri.queryParameters['name']);
        if (tab == null) return none;
        return WidgetRoute(WidgetRouteKind.tab, tab: tab);
      case 'done':
        final id = uri.queryParameters['id'];
        if (id == null || id.isEmpty) return none;
        return WidgetRoute(WidgetRouteKind.done, emailId: id);
      default:
        return none;
    }
  }

  bool get isKnown => kind != WidgetRouteKind.unknown;
}
