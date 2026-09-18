// Zero-overflow policy, enforced mechanically across the device matrix.
//
// Every component here is pumped at 9 viewports x 3 font scales. An overflow
// at any of them fails the test, so a regression cannot reach a phone
// unnoticed.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:agent_amar/models/agent_analysis.dart';
import 'package:agent_amar/models/email.dart';
import 'package:agent_amar/models/user_state.dart';
import 'package:agent_amar/theme/app_theme.dart';
import 'package:agent_amar/theme/responsive.dart';
import 'package:agent_amar/widgets/deadline_card.dart';
import 'package:agent_amar/widgets/email_list_card.dart';
import 'package:agent_amar/widgets/system_status_bar.dart';

import 'responsive_harness.dart';

Email _email({
  String subject = LongText.subject,
  String sender = LongText.sender,
  DateTime? deadline,
  PrimaryCategory category = PrimaryCategory.actionRequired,
}) =>
    Email(
      id: 'e1',
      senderName: sender,
      senderEmail: LongText.email,
      subject: subject,
      body: LongText.reply,
      snippet: LongText.reply,
      receivedAt: DateTime(2026, 9, 1, 9, 30),
      primaryCategory: category,
      analysis: AgentAnalysis(
        category: 'Compliance',
        priority: PriorityLevel.critical,
        actionRequired: true,
        reasoningSummary: 'r',
        deadline: deadline,
        actionDescription: LongText.deadline,
      ),
      userState: const UserState(isViewed: false),
    );

Widget _host(Widget child) => MaterialApp(
      theme: AppTheme.darkTheme,
      home: Scaffold(
        backgroundColor: AppColors.background,
        body: SingleChildScrollView(child: child),
      ),
    );

void main() {
  group('SystemStatusBar', () {
    for (final viewport in kAllViewports) {
      for (final scale in kTextScales) {
        testWidgets('${viewport.name} @ ${scale}x', (tester) async {
          await expectNoOverflow(
            tester,
            () => _host(const SystemStatusBar(
              backendOnline: false,
              // The longest label set the widget can produce.
              llmStatus: 'unconfigured',
              llmProvider: 'ollama',
              llmModel: 'qwen2.5-coder:14b-instruct-q4_K_M',
            )),
            viewport: viewport,
            textScale: scale,
          );
        });
      }
    }
  });

  group('EmailListCard with long content', () {
    for (final viewport in kAllViewports) {
      for (final scale in kTextScales) {
        testWidgets('${viewport.name} @ ${scale}x', (tester) async {
          await expectNoOverflow(
            tester,
            () => _host(EmailListCard(email: _email(), onTap: () {})),
            viewport: viewport,
            textScale: scale,
          );
        });
      }
    }
  });

  group('DeadlineCard with long title', () {
    for (final viewport in kAllViewports) {
      for (final scale in kTextScales) {
        testWidgets('${viewport.name} @ ${scale}x', (tester) async {
          await expectNoOverflow(
            tester,
            () => _host(DeadlineCard(
              email: _email(deadline: DateTime.now().add(const Duration(hours: 3))),
              onOpen: () {},
              onMarkDone: () {},
            )),
            viewport: viewport,
            textScale: scale,
          );
        });
      }
    }
  });

  group('responsive primitives', () {
    test('breakpoints follow available width, not device', () {
      expect(Breakpoint.fromWidth(320), Breakpoint.narrowPhone);
      expect(Breakpoint.fromWidth(374), Breakpoint.narrowPhone);
      expect(Breakpoint.fromWidth(375), Breakpoint.phone);
      expect(Breakpoint.fromWidth(599), Breakpoint.phone);
      expect(Breakpoint.fromWidth(600), Breakpoint.tablet);
      expect(Breakpoint.fromWidth(1023), Breakpoint.tablet);
      expect(Breakpoint.fromWidth(1024), Breakpoint.wide);
    });

    test('a split-screen tablet gets the phone layout', () {
      // The whole point of width-based breakpoints.
      expect(SortedLayout.fromWidth(400).isCompact, isTrue);
      expect(SortedLayout.fromWidth(400).gridColumns, 1);
      // ...and a landscape phone can use the wider one.
      expect(SortedLayout.fromWidth(800).isTabletOrWider, isTrue);
    });

    test('gutters grow with the breakpoint', () {
      expect(SortedLayout.fromWidth(320).pageGutter, Gap.md);
      expect(SortedLayout.fromWidth(390).pageGutter, Gap.lg);
      expect(SortedLayout.fromWidth(800).pageGutter, Gap.xl);
      expect(SortedLayout.fromWidth(1280).pageGutter, Gap.xxl);
    });

    testWidgets('AdaptiveRow stacks when narrow and rows when wide',
        (tester) async {
      Future<void> pumpAt(double width) async {
        await tester.pumpWidget(MaterialApp(
          home: Scaffold(
            body: SizedBox(
              width: width,
              child: const AdaptiveRow(children: [Text('a'), Text('b')]),
            ),
          ),
        ));
      }

      await pumpAt(360);
      expect(find.byType(Column), findsWidgets);

      await pumpAt(900);
      expect(find.byType(Row), findsWidgets);
    });

    testWidgets('BoundedTextScale honours scaling but caps the extreme',
        (tester) async {
      late TextScaler seen;
      await tester.pumpWidget(MediaQuery(
        data: const MediaQueryData(textScaler: TextScaler.linear(3.0)),
        child: BoundedTextScale(
          child: Builder(builder: (context) {
            seen = MediaQuery.textScalerOf(context);
            return const SizedBox();
          }),
        ),
      ));
      // 3.0x is clamped to the 1.8x ceiling, not discarded.
      expect(seen.scale(10), lessThanOrEqualTo(18.0));
      expect(seen.scale(10), greaterThan(10.0));
    });
  });
}
