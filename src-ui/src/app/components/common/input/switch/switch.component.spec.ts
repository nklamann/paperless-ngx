import { ComponentFixture, TestBed } from '@angular/core/testing'
import { SwitchComponent } from './switch.component'
import {
  FormsModule,
  NG_VALUE_ACCESSOR,
  ReactiveFormsModule,
} from '@angular/forms'

describe('SwitchComponent', () => {
  let component: SwitchComponent
  let fixture: ComponentFixture<SwitchComponent>
  let input: HTMLInputElement

  beforeEach(async () => {
    TestBed.configureTestingModule({
      declarations: [SwitchComponent],
      providers: [],
      imports: [FormsModule, ReactiveFormsModule],
    }).compileComponents()

    fixture = TestBed.createComponent(SwitchComponent)
    fixture.debugElement.injector.get(NG_VALUE_ACCESSOR)
    component = fixture.componentInstance
    fixture.detectChanges()
    input = component.inputField.nativeElement
  })

  it('should support use of checkbox', () => {
    input.checked = true
    input.dispatchEvent(new Event('change'))
    fixture.detectChanges()
    expect(component.value).toBeTruthy()

    input.checked = false
    input.dispatchEvent(new Event('change'))
    fixture.detectChanges()
    expect(component.value).toBeFalsy()
  })
})
