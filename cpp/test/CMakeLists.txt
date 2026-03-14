include(FetchContent)
FetchContent_Declare(doctest
    GIT_REPOSITORY https://github.com/doctest/doctest.git
    GIT_TAG        v2.4.11
    GIT_SHALLOW    TRUE
)
FetchContent_Declare(nlohmann_json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG        v3.11.3
    GIT_SHALLOW    TRUE
)
FetchContent_MakeAvailable(doctest nlohmann_json)

add_executable(embedded_bridge_tests
    test_main.cpp
    test_crc16.cpp
    test_hdlc.cpp
    test_slip.cpp
    test_cobs.cpp
    test_message.cpp
    test_wire.cpp
    test_multi_include.cpp
)
target_link_libraries(embedded_bridge_tests doctest embedded_bridge nlohmann_json::nlohmann_json)
target_compile_features(embedded_bridge_tests PRIVATE cxx_std_17)
target_compile_options(embedded_bridge_tests PRIVATE
    -Wall -Wextra -Wpedantic
)
target_compile_definitions(embedded_bridge_tests PRIVATE
    WIRE_TESTS_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../../wire-tests"
)
add_test(NAME embedded_bridge_tests COMMAND embedded_bridge_tests)
