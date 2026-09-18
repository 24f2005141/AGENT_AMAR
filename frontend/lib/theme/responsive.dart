import 'package:flutter/material.dart';

/// Sorted's responsive layout system.
///
/// The rule everywhere in the app: **decide from the available width, never
/// from the device**. A tablet in split-screen is 400px wide and must get the
/// phone layout; a phone in landscape is 800px wide and can use the wide one.
/// So every helper here reads the constraints/MediaQuery, and nothing asks
/// "is this a tablet model".
///
/// Usage:
/// ```dart
/// final layout = context.layout;   // from MediaQuery
/// if (layout.isCompact) ...        // stack vertically
/// padding: layout.pagePadding      // consistent gutters
/// ```
/// Inside a constrained box (a card, a sheet, a split pane) prefer
/// `LayoutBuilder` + [SortedLayout.fromWidth] so the decision follows the
/// space that widget actually has, not the whole window.
enum Breakpoint {
  /// ~320-374: the smallest phones still in use. Everything must fit here.
  narrowPhone,

  /// ~375-599: the mainstream phone.
  phone,

  /// ~600-1023: tablet portrait, phone landscape, split-screen tablet.
  tablet,

  /// ~1024+: tablet landscape, desktop-class Android, opened foldables.
  wide;

  static Breakpoint fromWidth(double width) {
    if (width < 375) return Breakpoint.narrowPhone;
    if (width < 600) return Breakpoint.phone;
    if (width < 1024) return Breakpoint.tablet;
    return Breakpoint.wide;
  }
}

/// A consistent spacing scale. Using these instead of ad-hoc numbers is what
/// keeps density coherent when a layout reflows between breakpoints.
abstract final class Gap {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 20;
  static const double xxl = 24;
  static const double xxxl = 32;
}

/// Resolved layout facts for the current context.
@immutable
class SortedLayout {
  final double width;
  final Breakpoint breakpoint;

  const SortedLayout._(this.width, this.breakpoint);

  factory SortedLayout.fromWidth(double width) =>
      SortedLayout._(width, Breakpoint.fromWidth(width));

  factory SortedLayout.of(BuildContext context) =>
      SortedLayout.fromWidth(MediaQuery.sizeOf(context).width);

  /// True when the layout must stack vertically rather than sit side by side.
  /// This - not "is phone" - is the question almost every widget should ask.
  bool get isCompact =>
      breakpoint == Breakpoint.narrowPhone || breakpoint == Breakpoint.phone;

  bool get isNarrow => breakpoint == Breakpoint.narrowPhone;

  bool get isTabletOrWider =>
      breakpoint == Breakpoint.tablet || breakpoint == Breakpoint.wide;

  bool get isWide => breakpoint == Breakpoint.wide;

  /// Horizontal page gutters: tighter on a 320px screen, generous on a tablet.
  double get pageGutter {
    switch (breakpoint) {
      case Breakpoint.narrowPhone:
        return Gap.md;
      case Breakpoint.phone:
        return Gap.lg;
      case Breakpoint.tablet:
        return Gap.xl;
      case Breakpoint.wide:
        return Gap.xxl;
    }
  }

  EdgeInsets get pagePadding =>
      EdgeInsets.symmetric(horizontal: pageGutter, vertical: Gap.md);

  /// Spacing between cards in a list.
  double get cardSpacing => isNarrow ? Gap.sm : Gap.md;

  /// Inner padding of a card.
  EdgeInsets get cardPadding => EdgeInsets.all(isNarrow ? Gap.md : Gap.lg);

  /// The widest a column of running text should get. Prose stretched across a
  /// 1280px tablet is unreadable, so wide layouts centre within this instead.
  double get readableMaxWidth => 720;

  /// How many columns a card grid should use for the available width.
  int get gridColumns {
    switch (breakpoint) {
      case Breakpoint.narrowPhone:
      case Breakpoint.phone:
        return 1;
      case Breakpoint.tablet:
        return 2;
      case Breakpoint.wide:
        return 3;
    }
  }
}

extension SortedLayoutContext on BuildContext {
  /// Layout facts for the whole window.
  SortedLayout get layout => SortedLayout.of(this);

  /// True when the on-screen keyboard is covering part of the window - forms
  /// must stay scrollable and the focused field reachable.
  bool get keyboardVisible => MediaQuery.viewInsetsOf(this).bottom > 0;
}

/// Centres and width-limits running text on large screens while staying full
/// width (minus gutters) on phones.
class ReadableWidth extends StatelessWidget {
  final Widget child;
  final double? maxWidth;

  const ReadableWidth({super.key, required this.child, this.maxWidth});

  @override
  Widget build(BuildContext context) {
    final limit = maxWidth ?? context.layout.readableMaxWidth;
    return Center(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: limit),
        child: child,
      ),
    );
  }
}

/// Lays children out in a [Row] when there is room and a [Column] when there
/// is not - the single most common reflow in this app (title + status +
/// actions). Using one widget for it keeps the behaviour identical everywhere
/// instead of each screen inventing its own breakpoint.
class AdaptiveRow extends StatelessWidget {
  final List<Widget> children;

  /// Force a specific direction; defaults to deciding from the width the
  /// widget is actually given.
  final bool? stacked;
  final double spacing;
  final CrossAxisAlignment rowCrossAxisAlignment;
  final CrossAxisAlignment columnCrossAxisAlignment;
  final MainAxisAlignment rowMainAxisAlignment;

  /// Below this width the children stack. Defaults to the phone breakpoint.
  final double stackBelow;

  const AdaptiveRow({
    super.key,
    required this.children,
    this.stacked,
    this.spacing = Gap.md,
    this.rowCrossAxisAlignment = CrossAxisAlignment.center,
    this.columnCrossAxisAlignment = CrossAxisAlignment.start,
    this.rowMainAxisAlignment = MainAxisAlignment.start,
    this.stackBelow = 600,
  });

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final stack = stacked ??
            (constraints.hasBoundedWidth && constraints.maxWidth < stackBelow);
        if (stack) {
          return Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: columnCrossAxisAlignment,
            children: _withGaps(children, spacing, vertical: true),
          );
        }
        return Row(
          crossAxisAlignment: rowCrossAxisAlignment,
          mainAxisAlignment: rowMainAxisAlignment,
          children: _withGaps(children, spacing, vertical: false),
        );
      },
    );
  }

  static List<Widget> _withGaps(
    List<Widget> items,
    double spacing, {
    required bool vertical,
  }) {
    final out = <Widget>[];
    for (var i = 0; i < items.length; i++) {
      out.add(items[i]);
      if (i != items.length - 1) {
        out.add(vertical ? SizedBox(height: spacing) : SizedBox(width: spacing));
      }
    }
    return out;
  }
}

/// A bottom sheet body that cannot overflow: it never exceeds the available
/// height, scrolls when its content is taller, and lifts clear of the
/// keyboard.
///
/// Every sheet in the app uses this rather than hand-rolling
/// `MediaQuery.viewInsets` maths, which is where the phone overflows came
/// from.
class AdaptiveSheet extends StatelessWidget {
  final Widget child;
  final EdgeInsets? padding;

  /// Fraction of screen height the sheet may occupy before it starts to
  /// scroll internally.
  final double maxHeightFactor;

  const AdaptiveSheet({
    super.key,
    required this.child,
    this.padding,
    this.maxHeightFactor = 0.9,
  });

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    final layout = context.layout;
    return SafeArea(
      top: false,
      child: ConstrainedBox(
        constraints: BoxConstraints(
          maxHeight: media.size.height * maxHeightFactor,
          // A sheet on a wide tablet should not run the full 1280px.
          maxWidth: layout.isTabletOrWider ? 640 : double.infinity,
        ),
        child: Padding(
          // Only the keyboard inset here; visual padding is the caller's.
          padding: EdgeInsets.only(bottom: media.viewInsets.bottom),
          child: SingleChildScrollView(
            padding: padding ??
                EdgeInsets.fromLTRB(
                  layout.pageGutter,
                  Gap.lg,
                  layout.pageGutter,
                  Gap.xxl,
                ),
            child: child,
          ),
        ),
      ),
    );
  }
}

/// Clamps how far the system font scale can stretch the UI.
///
/// This is NOT "disable font scaling": scaling up to [max] is honoured in
/// full, which covers Android's normal accessibility range. Beyond that text
/// would consume the whole viewport and the app becomes unusable, so the
/// growth is capped rather than the layout breaking.
class BoundedTextScale extends StatelessWidget {
  final Widget child;
  final double max;

  const BoundedTextScale({super.key, required this.child, this.max = 1.8});

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    return MediaQuery(
      data: media.copyWith(
        textScaler: media.textScaler.clamp(maxScaleFactor: max),
      ),
      child: child,
    );
  }
}

/// Wraps an existing bottom-sheet body so it can never exceed the screen.
///
/// The sheets in this app draw their own rounded surface and padding, so they
/// cannot simply be replaced by [AdaptiveSheet]. This shell adds only the two
/// things they were missing: a height ceiling and internal scrolling, which is
/// what makes them survive a short screen, landscape, a large font, or the
/// keyboard.
class ScrollableSheetShell extends StatelessWidget {
  final Widget child;
  final double maxHeightFactor;

  const ScrollableSheetShell({
    super.key,
    required this.child,
    this.maxHeightFactor = 0.9,
  });

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    return ConstrainedBox(
      constraints: BoxConstraints(
        maxHeight: media.size.height * maxHeightFactor,
      ),
      child: SingleChildScrollView(
        // The sheet body already applies its own visual padding; this only
        // keeps the content clear of the keyboard.
        padding: EdgeInsets.only(bottom: media.viewInsets.bottom),
        child: child,
      ),
    );
  }
}
