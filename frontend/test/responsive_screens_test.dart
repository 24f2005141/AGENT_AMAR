// Zero-overflow policy for whole screens and the sheets/dialogs layered on
// them - including with the keyboard open, which is where phone forms
// classically break.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:agent_amar/models/agent_analysis.dart';
import 'package:agent_amar/models/email.dart';
import 'package:agent_amar/models/user_state.dart';
import 'package:agent_amar/screens/login_screen.dart';
import 'package:agent_amar/services/auth_session_store.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/state/auth_controller.dart';
import 'package:agent_amar/theme/app_theme.dart';
import 'package:agent_amar/theme/responsive.dart';
import 'package:agent_amar/widgets/attention_email_card.dart';

import 'responsive_harness.dart';

Email _email({DateTime? deadline}) => Email(
      id: 'e1',
      senderName: LongText.sender,
      senderEmail: LongText.email,
      subject: LongText.subject,
      body: LongText.reply,
      snippet: LongText.reply,
      receivedAt: DateTime(2026, 9, 1, 9, 30),
      primaryCategory: PrimaryCategory.actionRequired,
      analysis: AgentAnalysis(
        category: 'Compliance & Regulatory Filings',
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
      home: child,
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() => SharedPreferences.setMockInitialValues({}));

  group('AttentionEmailCard - the busiest card in the app', () {
    for (final viewport in kAllViewports) {
      for (final scale in kTextScales) {
        testWidgets('${viewport.name} @ ${scale}x', (tester) async {
          await expectNoOverflow(
            tester,
            () => _host(Scaffold(
              backgroundColor: AppColors.background,
              body: SingleChildScrollView(
                child: AttentionEmailCard(
                  email: _email(
                      deadline: DateTime.now().add(const Duration(hours: 5))),
                  isFeatured: true,
                  onOpen: () {},
                  onMarkComplete: () {},
                  onRemindMe: () {},
                  onSnooze: () {},
                ),
              ),
            )),
            viewport: viewport,
            textScale: scale,
          );
        });
      }
    }
  });

  group('LoginScreen', () {
    // Phones only get the keyboard treatment; the point is that the form stays
    // reachable when the keyboard claims half the screen.
    for (final viewport in kAllViewports) {
      testWidgets(viewport.name, (tester) async {
        final auth = AuthController(
          repository: MockEmailRepository(),
          store: InMemoryAuthSessionStore(),
        );
        await expectNoOverflow(
          tester,
          () => _host(LoginScreen(authController: auth)),
          viewport: viewport,
        );
        auth.dispose();
      });
    }

    for (final viewport in kPhones) {
      testWidgets('${viewport.name} with keyboard open', (tester) async {
        final auth = AuthController(
          repository: MockEmailRepository(),
          store: InMemoryAuthSessionStore(),
        );
        await expectNoOverflow(
          tester,
          () => _host(LoginScreen(authController: auth)),
          viewport: viewport,
          // A typical Android IME height.
          keyboardInset: 320,
          reason: 'login form must survive the keyboard',
        );
        auth.dispose();
      });
    }

    testWidgets('stays scrollable so fields remain reachable', (tester) async {
      final auth = AuthController(
        repository: MockEmailRepository(),
        store: InMemoryAuthSessionStore(),
      );
      await expectNoOverflow(
        tester,
        () => _host(LoginScreen(authController: auth)),
        viewport: const DeviceViewport('320x640', Size(320, 640)),
        textScale: 2.0,
        keyboardInset: 320,
      );
      // A scrollable is what makes a tall form usable on a short screen.
      expect(find.byType(Scrollable), findsWidgets);
      auth.dispose();
    });
  });

  group('AdaptiveSheet - every bottom sheet is built on this', () {
    for (final viewport in kPhones) {
      testWidgets('${viewport.name} with long content and keyboard',
          (tester) async {
        await expectNoOverflow(
          tester,
          () => _host(Scaffold(
            body: AdaptiveSheet(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(LongText.subject),
                  const SizedBox(height: Gap.lg),
                  const Text(LongText.reply),
                  const SizedBox(height: Gap.lg),
                  ...List.generate(
                    8,
                    (i) => Padding(
                      padding: const EdgeInsets.only(bottom: Gap.sm),
                      child: Text('Option $i - ${LongText.deadline}'),
                    ),
                  ),
                ],
              ),
            ),
          )),
          viewport: viewport,
          textScale: 2.0,
          keyboardInset: 300,
        );
      });
    }

    testWidgets('does not stretch across a wide tablet', (tester) async {
      tester.view.physicalSize = const Size(1280, 800);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(_host(const Scaffold(
        body: AdaptiveSheet(child: Text('content')),
      )));
      await tester.pump();

      // Measure the sheet's scrollable content, not the Scaffold around it.
      final width = tester.getSize(find.byType(SingleChildScrollView)).width;
      // Readable width, not the full 1280px.
      expect(width, lessThanOrEqualTo(640));
    });
  });

  group('ReadableWidth', () {
    testWidgets('is full width on a phone and capped on a tablet',
        (tester) async {
      Future<double> widthAt(Size size) async {
        tester.view.physicalSize = size;
        tester.view.devicePixelRatio = 1.0;
        addTearDown(tester.view.reset);
        await tester.pumpWidget(_host(Scaffold(
          body: ReadableWidth(
            child: Container(key: const Key('body'), color: Colors.red),
          ),
        )));
        await tester.pump();
        return tester.getSize(find.byKey(const Key('body'))).width;
      }

      expect(await widthAt(const Size(360, 800)), 360);
      // Prose does not run the full width of a large tablet.
      expect(await widthAt(const Size(1280, 800)), lessThanOrEqualTo(720));
    });
  });
}
