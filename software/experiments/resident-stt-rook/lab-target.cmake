if(NOT TARGET rook-resident-whisper)
    add_executable(rook-resident-whisper "${CMAKE_CURRENT_LIST_DIR}/native_worker.cpp")
    target_compile_features(rook-resident-whisper PRIVATE cxx_std_17)
    target_include_directories(rook-resident-whisper PRIVATE
        "${CMAKE_SOURCE_DIR}/examples" "${CMAKE_BINARY_DIR}/rook-generated")
    target_link_libraries(rook-resident-whisper PRIVATE common whisper)
    set_target_properties(rook-resident-whisper PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
endif()
