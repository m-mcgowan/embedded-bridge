#include <doctest/doctest.h>
#include <embedded_bridge/detail/crc16.h>

using namespace ebridge::detail;

TEST_SUITE("CRC-16/HDLC") {

TEST_CASE("init value") {
    CHECK(CRC16_INIT == 0xFFFF);
}

TEST_CASE("known vector: '123456789'") {
    // Standard CRC-16/HDLC (IBM-SDLC, X.25) check value
    const uint8_t data[] = "123456789";
    uint16_t crc = crc16_hdlc(data, 9);
    CHECK(crc == 0x906E);
}

TEST_CASE("empty buffer") {
    // crc16_hdlc with len=0 returns INIT ^ xorout = 0xFFFF ^ 0xFFFF = 0
    const uint8_t dummy = 0;
    CHECK(crc16_hdlc(&dummy, 0) == 0x0000);
}

TEST_CASE("buffer helper matches incremental") {
    const uint8_t data[] = {0x01, 0x02, 0x03, 0x04};
    uint16_t incremental = CRC16_INIT;
    for (auto b : data) incremental = crc16_hdlc_update(incremental, b);
    // crc16_hdlc applies final XOR; incremental does not
    CHECK(crc16_hdlc(data, 4) == (incremental ^ 0xFFFF));
}

TEST_CASE("residue check for valid frame") {
    // Payload + CRC bytes (low byte first) → residue == CRC16_GOOD
    const uint8_t payload[] = {0x41, 0x42, 0x43};  // "ABC"
    uint16_t crc = crc16_hdlc(payload, 3);

    // HDLC sends CRC low byte first, high byte second
    uint8_t frame[5] = {0x41, 0x42, 0x43,
                        static_cast<uint8_t>(crc & 0xFF),
                        static_cast<uint8_t>(crc >> 8)};

    uint16_t residue = crc16_hdlc(frame, 5);
    CHECK(residue == CRC16_GOOD);
}

TEST_CASE("residue check with known vector") {
    const uint8_t data[] = "123456789";
    uint16_t crc = crc16_hdlc(data, 9);  // 0x906E

    uint8_t frame[11];
    for (int i = 0; i < 9; i++) frame[i] = data[i];
    frame[9] = static_cast<uint8_t>(crc & 0xFF);   // 0x6E
    frame[10] = static_cast<uint8_t>(crc >> 8);     // 0x90

    CHECK(crc16_hdlc(frame, 11) == CRC16_GOOD);
}

TEST_CASE("different data produces different CRC") {
    const uint8_t a[] = {0x01, 0x02};
    const uint8_t b[] = {0x01, 0x03};
    CHECK(crc16_hdlc(a, 2) != crc16_hdlc(b, 2));
}

TEST_CASE("single byte") {
    uint16_t crc = crc16_hdlc_update(CRC16_INIT, 0x00);
    CHECK(crc != CRC16_INIT);
}

}
