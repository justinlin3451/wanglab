#include <AccelStepper.h>

// Pin Configuration
const int stepPin = 3;
const int directionPin = 2;

// Stepper Motor Setup
AccelStepper stepper(AccelStepper::DRIVER, stepPin, directionPin);

// Motor Parameters (from recipe)
long motorSpeed = 4000;      // steps/second (default)
long targetPosition = 0;     // target position (P1/M1)
unsigned long motorTime = 0; // time duration in milliseconds

// Serial Communication
String inputBuffer = "";
const int MAX_POSITION = 30000;
const int MIN_POSITION = 0;
const int SERIAL_BAUD = 19200;

void setup() {
  Serial.begin(SERIAL_BAUD);

  // Configure stepper
  stepper.setPinsInverted(false, false, true);
  stepper.setMinPulseWidth(20);
  stepper.setMaxSpeed(8000);
  stepper.setAcceleration(1000);
  stepper.setCurrentPosition(0);

  delay(500);
}

void loop() {
  // Handle serial input
  if (Serial.available() > 0) {
    char ch = Serial.read();

    if (ch == '\n') {
      if (inputBuffer.length() > 0) {
        processCommand(inputBuffer);
        inputBuffer = "";
      }
    } else if (ch != '\r') {
      inputBuffer += ch;
    }
  }

  // Run stepper continuously
  stepper.run();

  // Check if movement is complete and time-based control is enabled
  if (stepper.distanceToGo() == 0 && motorTime > 0) {
    // Movement is complete, time-based actions can be handled here
  }
}

/*
 * Process incoming command from PyDriver
 * Format: <address> <transaction_id> <command> [value]
 * Examples:
 *   1 12345 reset_all
 *   1 12345 speed 5000
 *   1 12345 p1 15000
 *   1 12345 time 30000
 *   1 12345 get
 */
void processCommand(String cmd) {
  // Parse command string
  int space1 = cmd.indexOf(' ');
  if (space1 == -1) return;

  String addr = cmd.substring(0, space1);

  int space2 = cmd.indexOf(' ', space1 + 1);
  if (space2 == -1) return;

  String txid = cmd.substring(space1 + 1, space2);

  int space3 = cmd.indexOf(' ', space2 + 1);
  String command, value;

  if (space3 == -1) {
    command = cmd.substring(space2 + 1);
    value = "";
  } else {
    command = cmd.substring(space2 + 1, space3);
    value = cmd.substring(space3 + 1);
  }

  // Only process if address is correct (address 1)
  if (addr != "1") return;

  // Execute command
  if (command == "reset_all") {
    stepper.setCurrentPosition(0);
    motorSpeed = 4000;
    targetPosition = 0;
    motorTime = 0;
    sendResponse(addr, txid, "reset_all", "");
  }

  else if (command == "speed") {
    long newSpeed = value.toInt();
    if (newSpeed > 0 && newSpeed <= 8000) {
      motorSpeed = newSpeed;
      stepper.setMaxSpeed(motorSpeed);
      sendResponse(addr, txid, "speed", String(motorSpeed));
    }
  }

  else if (command == "M1" || command == "p1") {
    // M1 is the protocol command, p1 is the control name in PyDriver
    long newPos = value.toInt();
    // Constrain to valid range
    if (newPos < MIN_POSITION) newPos = MIN_POSITION;
    if (newPos > MAX_POSITION) newPos = MAX_POSITION;

    targetPosition = newPos;
    stepper.moveTo(targetPosition);
    sendResponse(addr, txid, command, String(targetPosition));
  }

  else if (command == "time") {
    // Time duration in milliseconds (from recipe)
    motorTime = value.toInt();
    sendResponse(addr, txid, "time", String(motorTime));
  }

  else if (command == "get") {
    long currentPos = stepper.currentPosition();
    // Send response: address txid get currentPosition
    Serial.print(addr);
    Serial.print(" ");
    Serial.print(txid);
    Serial.print(" get ");
    Serial.println(currentPos);
  }
}

/*
 * Send response to PyDriver
 * Format: <address> <transaction_id> <command> [value]
 */
void sendResponse(String addr, String txid, String cmd, String val) {
  Serial.print(addr);
  Serial.print(" ");
  Serial.print(txid);
  Serial.print(" ");
  Serial.print(cmd);
  if (val.length() > 0) {
    Serial.print(" ");
    Serial.print(val);
  }
  Serial.println();
}

/*
 * Utility Functions
 */

// Move to position and wait (blocking)
void moveToPosition(long position) {
  if (position < MIN_POSITION) position = MIN_POSITION;
  if (position > MAX_POSITION) position = MAX_POSITION;

  stepper.moveTo(position);
  while (stepper.distanceToGo() != 0) {
    stepper.run();
  }
}

// Get current position
long getCurrentPosition() {
  return stepper.currentPosition();
}

// Set motor speed
void setMotorSpeed(long speed) {
  if (speed > 0 && speed <= 8000) {
    motorSpeed = speed;
    stepper.setMaxSpeed(motorSpeed);
  }
}

