import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:agent_amar/main.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/state/inbox_controller.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Agent Amar app smoke and screen rendering test', (WidgetTester tester) async {
    SharedPreferences.setMockInitialValues({});

    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(() => tester.view.resetPhysicalSize());

    final controller = InboxController(
      repository: MockEmailRepository(),
      enableCountdownTimer: false,
    );

    await controller.loadData();

    await tester.pumpWidget(AgentAmarApp(
      controller: controller,
      enableNotifications: false,
    ));
    await tester.pump(const Duration(milliseconds: 100));

    // Verify Brand title is visible
    expect(find.text('AGENT AMAR'), findsOneWidget);
    // Verify Needs Attention section header is rendered
    expect(find.text('NEEDS ATTENTION'), findsOneWidget);
    // Verify Upcoming Deadlines section header is rendered
    expect(find.text('UPCOMING DEADLINES'), findsOneWidget);
  });
}
