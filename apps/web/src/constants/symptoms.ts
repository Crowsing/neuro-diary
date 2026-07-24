// Портовано байт-у-байт з docs/prototype/nd-v2.dc.html:
// get SYM(): рядки 975–989; get INT(): 990; get IMPACT(): 991.

import type { SymptomDef } from '../lib/types';

export const SYM: SymptomDef[] = [
  {id:'armWeak',name:'Слабкість у руці/руках',type:'scale',side:['Ліва','Права','Обидві'],impact:true},
  {id:'numb',name:'Оніміння кінцівок або пальців',type:'bool',side:['Ліва','Права','Обидві'],extra:{label:'Де саме',opts:['Руки','Ноги','Обличчя'],multi:true}},
  {id:'lowSense',name:'Знижена чутливість',type:'bool',side:['Ліва','Права','Обидві'],extra:{label:'Локалізація',opts:['Рука','Нога','Тулуб','Обличчя'],multi:true}},
  {id:'allodynia',name:'Підвищена чутливість / алодинія',type:'bool',extra:{label:'Тригер',opts:['Дотик','Одяг','Холод','Тепло'],multi:true}},
  {id:'vision',name:'Проблеми із зором',type:'scale',side:['Ліве око','Праве око','Обидва'],extra:{label:'Тип',opts:['Розмитість','Двоїння','Тьмяні кольори','Втрата поля','Біль при русі ока'],multi:true}},
  {id:'fatigue',name:'Втома',type:'scale',extra:{label:'Тип',opts:['Фізична','Когнітивна'],multi:true},impact:true},
  {id:'cramps',name:'М’язові спазми / крампи в ногах',type:'bool',side:['Ліва','Права','Обидві'],episodes:true,extra:{label:'Характер',opts:['Болючі','Нічні'],multi:true}},
  {id:'twitch',name:'Посмикування м’язів / пульсація',type:'bool',extra:{label:'Локалізація',opts:['Рука','Нога','Повіка','Інше'],multi:true}},
  {id:'bladder',name:'Проблеми із сечовипусканням',type:'bool',extra:{label:'Що саме',opts:['Позиви','Частота','Нетримання','Затримка','Нічні пробудження'],multi:true}},
  {id:'mood',name:'Пригніченість настрою',type:'scale'},
  {id:'cognition',name:'Концентрація / пам’ять',type:'scale',extra:{label:'Що було складно',opts:['Концентрація','Пам’ять','Пошук слів','Планування'],multi:true}},
  {id:'headache',name:'Головний біль',type:'scale',extra:{label:'Супутнє',opts:['Аура','Нудота','Блювання','Світлочутливість','Звукочутливість'],multi:true},impact:true},
  {id:'dizzy',name:'Запаморочення / хиткість',type:'bool',extra:{label:'Уточнення',opts:['Обертання','Переднепритомний стан','Порушення рівноваги'],multi:true}}
];

/** Підписи інтенсивності 1–5; індекс 0 — порожній (INT[v.int]). */
export const INT: string[] = ['','ледь помітно','легко','помірно','сильно','дуже сильно, суттєво заважає'];

/** Варіанти впливу на день. */
export const IMPACT: string[] = ['Не заважає','Помітно','Обмежує справи','Не можу виконувати звичні справи'];
