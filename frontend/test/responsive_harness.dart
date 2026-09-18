// Shared harness for the responsive test matrix.
//
// The point of this file: in a Flutter test a RenderFlex overflow raises a
// FlutterError, so pumping every screen at every size turns "looks fine on my
// tablet" into a mechanical pass/fail. That is what enforces the zero-overflow
// policy — not eyeballing.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// One entry in the device matrix.
@immutable
class DeviceViewport {
  final String name;
  final Size size;

  const DeviceViewport(this.name, this.size);

  DeviceViewport get landscape =>
      DeviceViewport('$name landscape', Size(size.height, size.width));
}

/// The sizes every screen must survive. Chosen to bracket real hardware:
/// the 320 entry is the hard floor, 1280x800 the tablet-landscape ceiling.
const List<DeviceViewport> kPhones = [
  DeviceViewport('320x640 narrow phone', Size(320, 640)),
  DeviceViewport('360x800 phone', Size(360, 800)),
  DeviceViewport('375x812 phone', Size(375, 812)),
  DeviceViewport('390x844 phone', Size(390, 844)),
  DeviceViewport('412x915 phone', Size(412, 915)),
  DeviceViewport('430x932 phone', Size(430, 932)),
];

const List<DeviceViewport> kTablets = [
  DeviceViewport('600x960 tablet', Size(600, 960)),
  DeviceViewport('800x1280 tablet', Size(800, 1280)),
  DeviceViewport('1280x800 tablet landscape', Size(1280, 800)),
];

List<DeviceViewport> get kAllViewports => [...kPhones, ...kTablets];

/// Font scales to exercise. 1.0 is default; 2.0 is Android's largest common
/// accessibility setting.
const List<double> kTextScales = [1.0, 1.5, 2.0];

/// Sets the test window to [viewport] with the given text scale, runs [body],
/// and asserts that nothing overflowed.
///
/// Any RenderFlex/RenderBox overflow during layout or paint surfaces through
/// `tester.takeException()`, so the assertion below is a genuine overflow
/// detector rather than a smoke test.
Future<void> expectNoOverflow(
  WidgetTester tester,
  Widget Function() build, {
  required DeviceViewport viewport,
  double textScale = 1.0,
  double keyboardInset = 0,
  String? reason,
}) async {
  tester.view.physicalSize = viewport.size;
  tester.view.devicePixelRatio = 1.0;
  tester.view.viewInsets = FakeViewPadding(bottom: keyboardInset);
  addTearDown(tester.view.reset);

  await tester.pumpWidget(
    MediaQuery(
      data: MediaQueryData(
        size: viewport.size,
        textScaler: TextScaler.linear(textScale),
        viewInsets: EdgeInsets.only(bottom: keyboardInset),
      ),
      child: build(),
    ),
  );
  await tester.pump(const Duration(milliseconds: 50));

  final error = tester.takeException();
  expect(
    error,
    isNull,
    reason: 'Layout failed at ${viewport.name} '
        '(textScale ${textScale}x'
        '${keyboardInset > 0 ? ', keyboard $keyboardInset' : ''})'
        '${reason == null ? '' : ' - $reason'}: $error',
  );
}

/// Content long enough to break a naive layout: a subject that will not fit on
/// one line, an address that will not fit in a chip, an AI reply that runs to
/// paragraphs.
abstract final class LongText {
  static const subject =
      'Reminder: your quarterly compliance attestation and supporting '
      'documentation must be submitted through the internal portal before the '
      'deadline listed in this message';

  static const sender =
      'Dr. Alexandra Konstantinopoulos-Wetherby, Faculty of Engineering';

  static const email =
      'alexandra.konstantinopoulos.wetherby@engineering.department.example.edu';

  static const reply =
      'Thank you for the detailed update. I have reviewed the attached '
      'documentation and everything looks consistent with what we discussed '
      'in the planning meeting. I will confirm the remaining items with the '
      'rest of the team and follow up with a consolidated response before the '
      'end of the week, including the revised timeline you asked about.';

  static const deadline =
      'Final submission of the capstone project report, including appendices '
      'and the signed supervisor declaration form';
}
