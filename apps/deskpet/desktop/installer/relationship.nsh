!define DESKPET_INSTALL_HISTORY "Software\com.nailong.deskpet\Relationship"

!ifndef BUILD_UNINSTALLER
Var DeskpetPriorUserInstall
Var DeskpetPriorMachineInstall

!macro customInit
  ; Read the old registration before electron-builder removes the old installation.
  ReadRegStr $DeskpetPriorUserInstall HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation
  ReadRegStr $DeskpetPriorMachineInstall HKLM "${INSTALL_REGISTRY_KEY}" InstallLocation
  ClearErrors
!macroend

!macro customInstall
  ; SHELL_CONTEXT is HKCU for a current-user install and HKLM for all users.
  ; This key is independent of the product registration deleted by the uninstaller.
  ReadRegStr $0 SHELL_CONTEXT "${DESKPET_INSTALL_HISTORY}" FirstInstalledAt
  ${If} $0 == ""
    ${If} $installMode == "all"
      StrCpy $0 $DeskpetPriorMachineInstall
    ${Else}
      StrCpy $0 $DeskpetPriorUserInstall
    ${EndIf}
    ${If} $0 == ""
      ${GetTime} "" "LS" $0 $1 $2 $3 $4 $5 $6
      ${If} ${Errors}
        SetErrorLevel 1
        Abort "无法读取首次安装时间。"
      ${EndIf}
      StrCpy $0 "$2-$1-$0T$4:$5:$6Z"
    ${Else}
      ; Old installers did not record a date. Existing profiles use SQLite first_seen.
      StrCpy $0 "legacy"
    ${EndIf}
    ClearErrors
    WriteRegStr SHELL_CONTEXT "${DESKPET_INSTALL_HISTORY}" FirstInstalledAt $0
    ${If} ${Errors}
      SetErrorLevel 1
      Abort "无法保存首次安装时间。"
    ${EndIf}
  ${EndIf}
!macroend
!endif
